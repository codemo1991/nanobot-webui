"""Feishu/Lark channel implementation using lark-oapi SDK with WebSocket long connection."""

import asyncio
import functools
import json
import re
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import FeishuConfig

try:
    import lark_oapi as lark
    from lark_oapi.event.custom import CustomizedEvent
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
        GetMessageResourceRequest,
        P2ImMessageReceiveV1,
        PatchMessageRequest,
        PatchMessageRequestBody,
    )
    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    lark = None
    Emoji = None
    CustomizedEvent = None  # type: ignore

def _media_dir(workspace: "Path | None") -> Path:
    """返回 media 目录：workspace 优先，否则 ~/.nanobot/media"""
    if workspace:
        return Path(workspace).resolve() / ".nanobot" / "media"
    return Path.home() / ".nanobot" / "media"


def _markdown_to_feishu_post(text: str) -> dict[str, Any]:
    """
    将 Markdown 文本转换为飞书 post（富文本）格式。

    飞书 post 结构：
    {
        "zh_cn": {
            "content": [
                [  // 每个内层 list 是一个段落
                    {"tag": "text", "text": "...", "style": [...]},
                    {"tag": "a", "text": "...", "href": "..."},
                    {"tag": "code_block", "language": "...", "text": "..."},
                ]
            ]
        }
    }
    """
    if not text:
        return {"zh_cn": {"content": [[{"tag": "text", "text": ""}]]}}

    paragraphs: list[list[dict[str, Any]]] = []

    # 先提取代码块，防止内部内容被解析
    code_blocks: list[tuple[str, str]] = []

    def _save_code_block(m: re.Match) -> str:
        lang = m.group(1) or ""
        code = m.group(2)
        idx = len(code_blocks)
        code_blocks.append((lang, code))
        return f"\x00CODEBLOCK{idx}\x00"

    text = re.sub(r"```(\w*)\n?([\s\S]*?)```", _save_code_block, text)

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # 代码块占位符
        cb_match = re.match(r"^\x00CODEBLOCK(\d+)\x00$", line.strip())
        if cb_match:
            idx = int(cb_match.group(1))
            lang, code = code_blocks[idx]
            paragraphs.append([{
                "tag": "code_block",
                "language": lang or "plain_text",
                "text": code,
            }])
            i += 1
            continue

        # 空行
        if not line.strip():
            i += 1
            continue

        # 标题行 -> 加粗段落
        header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if header_match:
            header_text = header_match.group(2)
            paragraphs.append([{"tag": "text", "text": header_text, "style": ["bold"]}])
            i += 1
            continue

        # 引用行 > text
        quote_match = re.match(r"^>\s*(.*)", line)
        if quote_match:
            quote_text = quote_match.group(1)
            paragraphs.append([{"tag": "text", "text": f"│ {quote_text}", "style": ["italic"]}])
            i += 1
            continue

        # 普通段落：解析 inline 格式
        elements = _parse_inline_elements(line)
        if elements:
            paragraphs.append(elements)
        i += 1

    if not paragraphs:
        paragraphs = [[{"tag": "text", "text": text}]]

    return {"zh_cn": {"content": paragraphs}}


def _parse_inline_elements(line: str) -> list[dict[str, Any]]:
    """解析一行文本中的 inline 格式元素（加粗、斜体、链接、行内代码、列表）。"""
    # 列表项
    list_match = re.match(r"^[-*]\s+(.+)$", line)
    if list_match:
        line = f"• {list_match.group(1)}"

    # 有序列表
    ol_match = re.match(r"^(\d+)\.\s+(.+)$", line)
    if ol_match:
        line = f"{ol_match.group(1)}. {ol_match.group(2)}"

    elements: list[dict[str, Any]] = []

    # 使用正则分段匹配各种 inline 格式
    pattern = re.compile(
        r"(`[^`]+`)"            # inline code
        r"|(\[([^\]]+)\]\(([^)]+)\))"  # link [text](url)
        r"|(\*\*(.+?)\*\*)"    # bold **text**
        r"|(\*(.+?)\*)"        # italic *text*
        r"|(~~(.+?)~~)"        # strikethrough ~~text~~
    )

    last_end = 0
    for m in pattern.finditer(line):
        # 前面的纯文本
        if m.start() > last_end:
            elements.append({"tag": "text", "text": line[last_end:m.start()]})

        if m.group(1):  # inline code
            code_text = m.group(1)[1:-1]
            elements.append({"tag": "text", "text": code_text, "style": ["bold"]})
        elif m.group(2):  # link
            elements.append({"tag": "a", "text": m.group(3), "href": m.group(4)})
        elif m.group(5):  # bold
            elements.append({"tag": "text", "text": m.group(6), "style": ["bold"]})
        elif m.group(7):  # italic
            elements.append({"tag": "text", "text": m.group(8), "style": ["italic"]})
        elif m.group(9):  # strikethrough
            elements.append({"tag": "text", "text": m.group(10), "style": ["lineThrough"]})

        last_end = m.end()

    # 剩余纯文本
    if last_end < len(line):
        elements.append({"tag": "text", "text": line[last_end:]})

    return elements if elements else [{"tag": "text", "text": line}]


# 卡片内容最大长度（字符），超出时截断
_CARD_CONTENT_MAX = 8000


def _build_card_json(
    body_md: str,
    header_text: str = "AI 助手",
    template: str = "blue",
) -> str:
    """
    构建飞书交互卡片（Card 1.0）JSON 字符串。

    body_md: 卡片正文，支持飞书 lark_md 格式（Markdown 子集）。
    template: 卡片标题颜色，可选 blue / green / red / yellow / grey 等。
    """
    if len(body_md) > _CARD_CONTENT_MAX:
        body_md = body_md[:_CARD_CONTENT_MAX] + "\n\n...(内容已截断)"

    card: dict[str, Any] = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_text},
            "template": template,
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": body_md},
            }
        ],
    }
    return json.dumps(card, ensure_ascii=False)


class FeishuChannel(BaseChannel):
    """
    Feishu/Lark channel using WebSocket long connection.

    Uses WebSocket to receive events - no public IP or webhook required.

    Supports:
    - Text messages
    - Image messages (download + image recognition)
    - Rich text (post) messages (parse text + images)
    - Rich text (post) reply format

    Requires:
    - App ID and App Secret from Feishu Open Platform
    - Bot capability enabled
    - Event subscription enabled (im.message.receive_v1)
    - im:message、im:resource 权限
    """

    name = "feishu"

    def __init__(
        self,
        config: FeishuConfig,
        bus: MessageBus,
        workspace: "Path | None" = None,
        agent: Any = None,
    ):
        super().__init__(config, bus)
        self.config: FeishuConfig = config
        self._workspace = workspace
        self._agent = agent  # 用于 /stop 等命令
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._loop: asyncio.AbstractEventLoop | None = None
        # chat_id -> 进度卡片 message_id 队列（FIFO，同一会话并发请求时按顺序匹配）
        self._active_progress_cards: dict[str, deque[str]] = {}
        # chat_id -> 上次 patch 时间戳（用于节流控制）
        self._last_card_update: dict[str, float] = {}
        # chat_id -> 子 Agent 独立进度卡片 message_id
        self._subagent_cards: dict[str, str] = {}
        # chat_id -> 正在监听子 Agent 进度的 asyncio.Task
        self._subagent_watcher_tasks: dict[str, "asyncio.Task[None]"] = {}

    def setup_client(self) -> None:
        """初始化 Feishu Client（仅用于发送消息，不启动 WebSocket 监听）。"""
        if not FEISHU_AVAILABLE:
            logger.warning("Feishu SDK not installed. Run: pip install lark-oapi")
            return
        if not self.config.app_id or not self.config.app_secret:
            logger.warning("Feishu app_id/app_secret not configured, send-only client skipped")
            return
        self._client = lark.Client.builder() \
            .app_id(self.config.app_id) \
            .app_secret(self.config.app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()
        logger.debug("Feishu send-only client initialized")

    async def start(self) -> None:
        """Start the Feishu bot with WebSocket long connection."""
        if not FEISHU_AVAILABLE:
            logger.error("Feishu SDK not installed. Run: pip install lark-oapi")
            return

        if not self.config.app_id or not self.config.app_secret:
            logger.error("Feishu app_id and app_secret not configured")
            return

        self._running = True
        self._loop = asyncio.get_running_loop()

        _media_dir(self._workspace).mkdir(parents=True, exist_ok=True)

        self._client = lark.Client.builder() \
            .app_id(self.config.app_id) \
            .app_secret(self.config.app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()

        builder = lark.EventDispatcherHandler.builder(
            self.config.encrypt_key or "",
            self.config.verification_token or "",
        ).register_p2_im_message_receive_v1(self._on_message_sync)

        def _noop_message_read(data: Any) -> None:
            pass

        if CustomizedEvent is not None:
            register_customized = getattr(builder, "register_p2_customized_event", None)
            if register_customized is not None:
                for evt in ("im.message.message_read_v1", "im.message.message_read"):
                    try:
                        builder = register_customized(evt, _noop_message_read)
                        logger.info(f"Registered no-op handler for {evt}")
                        break
                    except Exception as e:
                        logger.debug(f"Could not register {evt}: {e}")

        event_handler = builder.build()

        self._ws_client = lark.ws.Client(
            self.config.app_id,
            self.config.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        def run_ws():
            try:
                self._ws_client.start()
            except Exception:
                logger.exception("Feishu WebSocket error")

        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()

        logger.info("Feishu bot started with WebSocket long connection")

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the Feishu bot."""
        self._running = False
        if self._ws_client:
            try:
                self._ws_client.stop()
            except Exception as e:
                logger.warning(f"Error stopping WebSocket client: {e}")
        logger.info("Feishu bot stopped")

    # ── 发送消息 ───────────────────────────────────────────────

    async def send(self, msg: OutboundMessage) -> None:
        """发送消息。若存在进度卡片则直接更新为最终结果（绿色），否则新建 post 消息。"""
        if not self._client:
            logger.warning("Feishu client not initialized")
            return

        # 去除消息前导/尾随换行，避免飞书显示多余空行
        body_text = (msg.content or "").strip()

        # 尝试将进度卡片更新为最终回复（FIFO：取最早创建的卡片，避免并发时回复错配）
        card_message_id = None
        if msg.chat_id in self._active_progress_cards:
            q = self._active_progress_cards[msg.chat_id]
            if q:
                card_message_id = q.popleft()
                if not q:
                    del self._active_progress_cards[msg.chat_id]
        self._last_card_update.pop(msg.chat_id, None)

        if card_message_id:
            loop = asyncio.get_running_loop()
            fn = functools.partial(
                self._patch_card_sync,
                card_message_id,
                body_text,
                "AI 助手",
                "green",
            )
            success = await loop.run_in_executor(None, fn)
            if success:
                logger.debug(f"Progress card updated with final result: {card_message_id}")
                return
            logger.warning("Final card patch failed, falling back to new post message")

        # 无进度卡片（或 patch 失败）时，发送新的 post 消息
        try:
            if msg.chat_id.startswith("oc_"):
                receive_id_type = "chat_id"
            else:
                receive_id_type = "open_id"

            # 将 markdown 转为飞书 post 格式
            post_body = _markdown_to_feishu_post(body_text)
            content = json.dumps(post_body, ensure_ascii=False)

            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(msg.chat_id)
                    .msg_type("post")
                    .content(content)
                    .build()
                ).build()

            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._client.im.v1.message.create, request
            )

            if not response.success():
                logger.warning(f"Feishu post message failed (code={response.code} msg={response.msg}), falling back to text")
                await self._send_text_fallback(msg.chat_id, body_text)
            else:
                logger.debug(f"Feishu post message sent to {msg.chat_id}")

        except Exception as e:
            logger.warning(f"Feishu post send error: {e}, falling back to text")
            await self._send_text_fallback(msg.chat_id, body_text)

    async def _send_text_fallback(self, chat_id: str, content: str) -> None:
        """纯文本发送回退。"""
        try:
            if chat_id.startswith("oc_"):
                receive_id_type = "chat_id"
            else:
                receive_id_type = "open_id"

            payload = json.dumps({"text": content})
            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(payload)
                    .build()
                ).build()

            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._client.im.v1.message.create, request
            )
            if not response.success():
                logger.error(f"Feishu text fallback failed: code={response.code}, msg={response.msg}")
        except Exception as e:
            logger.exception(f"Feishu text fallback error: {e}")

    # ── 进度卡片 ───────────────────────────────────────────────

    def _patch_card_sync(
        self,
        message_id: str,
        body_md: str,
        header_text: str,
        template: str,
    ) -> bool:
        """同步更新已发送的交互卡片内容（供 progress_callback 调用）。"""
        try:
            content = _build_card_json(body_md, header_text, template)
            request = PatchMessageRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(content)
                    .build()
                ).build()
            response = self._client.im.v1.message.patch(request)
            if not response.success():
                logger.debug(f"Card patch failed: code={response.code} msg={response.msg}")
                return False
            return True
        except Exception as e:
            logger.debug(f"Card patch error: {e}")
            return False

    async def _watch_subagent_progress(
        self, chat_id: str, receive_id_type: str
    ) -> None:
        """
        后台监听 SubagentProgressBus 中针对该飞书会话的子 Agent 进度事件，
        并通过新的交互卡片实时展示给用户。

        - subagent_start   → 发送新的子 Agent 进度卡片
        - subagent_progress → 节流 patch 卡片（Claude Code 输出）
        - subagent_end      → 更新卡片为最终状态

        最多等待 5 分钟无事件后自动退出。
        """
        import queue as _queue
        from nanobot.agent.subagent_progress import SubagentProgressBus

        origin_key = f"feishu:{chat_id}"
        bus = SubagentProgressBus.get()
        q = bus.subscribe(origin_key, replay=True)

        idle_timeout = 300.0  # 5 分钟
        last_event_time = time.time()
        last_patch_time = 0.0
        # task_id -> card_message_id
        task_cards: dict[str, str] = {}
        # task_id -> 最近的 claude_code 输出行（最多 25 行）
        task_lines: dict[str, list[str]] = {}

        _self = self

        async def _send_subagent_card(label: str, body_md: str, color: str = "blue") -> str | None:
            """发送新的子 Agent 进度卡片，返回 message_id。"""
            if not _self._client:
                return None
            try:
                content = _build_card_json(body_md, f"🤖 子 Agent: {label}", color)
                request = CreateMessageRequest.builder() \
                    .receive_id_type(receive_id_type) \
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type("interactive")
                        .content(content)
                        .build()
                    ).build()
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None, _self._client.im.v1.message.create, request
                )
                if response.success() and response.data and response.data.message_id:
                    return response.data.message_id
            except Exception as e:
                logger.debug(f"发送子 Agent 卡片失败: {e}")
            return None

        try:
            while True:
                try:
                    evt = q.get_nowait()
                except _queue.Empty:
                    await asyncio.sleep(0.5)
                    if time.time() - last_event_time >= idle_timeout:
                        break
                    continue

                last_event_time = time.time()
                evt_type = evt.get("type", "")
                task_id = evt.get("task_id", "")
                label = evt.get("label", "子 Agent")

                if evt_type == "subagent_start":
                    backend = evt.get("backend", "native")
                    task_preview = evt.get("task", "")[:80]
                    body = f"**正在执行任务...**\n\n> {task_preview}\n\n_后端: {backend}_"
                    card_id = await _send_subagent_card(label, body, "blue")
                    if card_id:
                        task_cards[task_id] = card_id
                        task_lines[task_id] = []
                    logger.debug(f"飞书子 Agent 卡片已发送: task_id={task_id} card={card_id}")

                elif evt_type == "subagent_progress":
                    card_id = task_cards.get(task_id)
                    if not card_id:
                        continue
                    subtype = evt.get("subtype", "")
                    content_text = evt.get("content", "")
                    tool_name = evt.get("tool_name", "")
                    lines = task_lines.setdefault(task_id, [])

                    if subtype == "tool_use" and tool_name:
                        lines.append(f"[{tool_name}] {content_text[:100]}")
                    elif subtype == "assistant_text" and content_text:
                        lines.append(content_text[:150])
                    elif subtype == "subagent_start":
                        lines.append(f"🤖 子任务: {content_text[:80]}")
                    else:
                        continue

                    if len(lines) > 25:
                        del lines[:-25]

                    # 节流：最多每 2 秒 patch 一次
                    now = time.time()
                    if now - last_patch_time < 2.0:
                        continue
                    last_patch_time = now

                    code_block = "\n".join(lines)
                    body = f"**⚙️ 执行中...**\n\n```\n{code_block}\n```"
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        functools.partial(_self._patch_card_sync, card_id, body, f"🤖 {label}", "orange"),
                    )

                elif evt_type == "subagent_end":
                    card_id = task_cards.pop(task_id, None)
                    task_lines.pop(task_id, None)
                    if not card_id:
                        continue
                    status = evt.get("status", "error")
                    summary = evt.get("summary", "")[:300]
                    if status == "ok":
                        body = f"**✅ 任务完成**\n\n{summary}"
                        color = "green"
                    elif status == "timeout":
                        body = f"**⏳ 任务超时**\n\n{summary}\n\n_任务可能仍在后台运行，请稍后查询状态_"
                        color = "orange"
                    else:
                        body = f"**❌ 任务失败**\n\n{summary}"
                        color = "red"
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        functools.partial(_self._patch_card_sync, card_id, body, f"🤖 {label}", color),
                    )
                    logger.debug(f"飞书子 Agent 卡片更新完成: task_id={task_id} status={status}")

                    # 如果所有任务都已结束且 idle，延迟退出
                    if not task_cards:
                        last_event_time = time.time() - (idle_timeout - 30)  # 再等 30 秒

        except Exception:
            logger.exception(f"飞书子 Agent 进度监听出错: chat_id={chat_id}")
        finally:
            bus.unsubscribe(origin_key, q)
            self._subagent_watcher_tasks.pop(chat_id, None)
            logger.debug(f"飞书子 Agent 进度监听退出: chat_id={chat_id}")

    async def _send_initial_progress_card(
        self, chat_id: str, receive_id_type: str
    ) -> str | None:
        """发送初始'思考中'进度卡片，返回消息 ID（失败返回 None）。"""
        try:
            content = _build_card_json("⚙️ 正在思考...", "AI 助手", "blue")
            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("interactive")
                    .content(content)
                    .build()
                ).build()

            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._client.im.v1.message.create, request
            )

            if response.success() and response.data and response.data.message_id:
                logger.debug(f"Progress card sent: {response.data.message_id}")
                return response.data.message_id
            else:
                logger.warning(f"Failed to send progress card: code={response.code} msg={response.msg}")
                return None
        except Exception as e:
            logger.warning(f"Error sending progress card: {e}")
            return None

    # ── 接收消息 ───────────────────────────────────────────────

    def _on_message_sync(self, data: "P2ImMessageReceiveV1") -> None:
        """Sync handler (WebSocket thread) → schedule async handler."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)

    async def _on_message(self, data: "P2ImMessageReceiveV1") -> None:
        """Handle incoming message from Feishu."""
        try:
            event = data.event
            message = event.message
            sender = event.sender

            message_id = message.message_id
            if message_id in self._processed_message_ids:
                logger.debug(f"[Feishu] Skipping duplicate message: {message_id}")
                return
            self._processed_message_ids[message_id] = None
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)

            msg_type = message.message_type
            logger.info(f"[Feishu] Processing message: {message_id}, type: {msg_type}")

            if sender.sender_type == "bot":
                return

            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id
            chat_type = message.chat_type

            await self._add_reaction(message_id, "OnIt")

            content_parts: list[str] = []
            media_paths: list[str] = []

            if msg_type == "text":
                content_parts.append(self._parse_text_content(message.content))

            elif msg_type == "image":
                image_path = await self._download_image_from_message(message_id, message.content)
                if image_path:
                    media_paths.append(image_path)
                    content_parts.append("[图片]")
                else:
                    content_parts.append("[图片: 下载失败]")

            elif msg_type == "post":
                text, images = await self._parse_post_content(message_id, message.content)
                if text:
                    content_parts.append(text)
                media_paths.extend(images)
                if images and not text:
                    content_parts.append("[富文本图片]")

            elif msg_type == "file":
                content_parts.append("[文件]")

            elif msg_type == "audio":
                audio_path = await self._download_audio_from_message(message_id, message.content)
                if audio_path:
                    media_paths.append(audio_path)
                    content_parts.append("[语音]")
                else:
                    content_parts.append("[语音下载失败]")

            elif msg_type == "sticker":
                content_parts.append("[表情]")

            else:
                content_parts.append(f"[{msg_type}]")

            content = "\n".join(content_parts) if content_parts else "[空消息]"
            if not content.strip() and not media_paths:
                return

            reply_to = chat_id if chat_type == "group" else sender_id
            receive_id_type = "chat_id" if reply_to.startswith("oc_") else "open_id"

            # 发送初始进度卡片
            card_message_id = await self._send_initial_progress_card(reply_to, receive_id_type)
            if card_message_id:
                if reply_to not in self._active_progress_cards:
                    self._active_progress_cards[reply_to] = deque()
                self._active_progress_cards[reply_to].append(card_message_id)
                self._last_card_update[reply_to] = 0.0

            # 构建进度回调闭包，负责实时更新进度卡片
            progress_callback = None
            if card_message_id:
                # 每个 step: {"name": str, "desc": str, "result": str}
                steps_done: list[dict[str, str]] = []
                step_current: list[dict[str, str]] = [{}]
                # Claude Code 滚动输出缓冲（最近 25 行）
                claude_lines: list[str] = []
                in_claude_code: list[bool] = [False]
                last_update_ts: list[float] = [0.0]
                _self = self

                def _fmt_args(tool_name: str, arguments: dict) -> str:
                    """从工具参数中提取人类可读的一行描述。"""
                    if not arguments:
                        return ""
                    # 常见字段优先级：路径 > 查询 > 命令 > prompt > url > 首个值
                    for key in ("path", "file_path", "relative_path"):
                        if key in arguments:
                            return str(arguments[key])
                    for key in ("query", "search_query"):
                        if key in arguments:
                            v = str(arguments[key])
                            return f'"{v[:80]}"'
                    if "command" in arguments:
                        return f'`{str(arguments["command"])[:80]}`'
                    if "prompt" in arguments:
                        v = str(arguments["prompt"])
                        return v[:80] + ("..." if len(v) > 80 else "")
                    if "url" in arguments:
                        return str(arguments["url"])[:80]
                    # 兜底：取第一个键值对
                    first_key = next(iter(arguments))
                    first_val = str(arguments[first_key])
                    return f"{first_key}={first_val[:60]}"

                _TOOL_ICON: dict[str, str] = {
                    "read_file": "📄",
                    "write_file": "✏️",
                    "edit_file": "✏️",
                    "search_web": "🌐",
                    "execute_command": "💻",
                    "claude_code": "🤖",
                    "mcp": "🔌",
                    "list_directory": "📁",
                    "image_recognition": "🖼️",
                }

                def _tool_icon(name: str) -> str:
                    for prefix, icon in _TOOL_ICON.items():
                        if name.startswith(prefix):
                            return icon
                    return "🔧"

                def _build_normal_card() -> None:
                    """构建普通 agent 工具调用进度卡片并 patch。"""
                    prog_lines: list[str] = []
                    total = len(steps_done) + (1 if step_current[0] else 0)
                    if total:
                        prog_lines.append(f"**已调用 {len(steps_done)}/{total} 个工具**\n")
                    for s in steps_done:
                        icon = _tool_icon(s["name"])
                        desc = f" — {s['desc']}" if s.get("desc") else ""
                        result_hint = f"\n> {s['result'][:80]}" if s.get("result") else ""
                        prog_lines.append(f"{icon} ~~`{s['name']}`~~{desc}{result_hint}")
                    if step_current[0]:
                        cur = step_current[0]
                        icon = _tool_icon(cur["name"])
                        desc = f" {cur['desc']}" if cur.get("desc") else ""
                        label = "Claude Code" if in_claude_code[0] else cur["name"]
                        prog_lines.append(f"{icon} **`{label}`**{desc} _执行中..._")

                    body_md = "\n".join(prog_lines) if prog_lines else "⚙️ 正在思考..."
                    _self._patch_card_sync(card_message_id, body_md, "AI 助手", "blue")

                def _on_progress(evt: dict[str, Any]) -> None:  # noqa: C901
                    evt_type = evt.get("type", "")

                    # ── Claude Code 实时输出 ──────────────────────
                    if evt_type == "claude_code_progress":
                        subtype = evt.get("subtype", "")
                        raw_line = evt.get("line", "")

                        if raw_line:
                            claude_lines.append(raw_line)
                        elif subtype == "subagent_start":
                            subagent_type = evt.get("subagent_type", "subagent")
                            content = evt.get("content", "")
                            label = {
                                "code-explorer": "🔍 探索",
                                "code-implementer": "⚒️ 实现",
                                "command-runner": "▶️ 执行",
                            }.get(subagent_type, f"🤖 {subagent_type}")
                            claude_lines.append(f"[并行 {label}] {content[:100]}")
                        elif subtype == "tool_use":
                            tool_name = evt.get("tool_name", "Tool")
                            content = evt.get("content", "")
                            claude_lines.append(f"[{tool_name}] {content[:120]}")
                        elif subtype == "assistant_text":
                            content = evt.get("content", "")
                            if content:
                                claude_lines.append(content[:150] + ("..." if len(content) > 150 else ""))
                        elif subtype == "waiting_user_decision":
                            claude_lines.append("⏳ 等待用户决策...")
                        else:
                            return

                        if len(claude_lines) > 25:
                            del claude_lines[:-25]

                        now = time.time()
                        if now - last_update_ts[0] < 2.0:
                            return
                        last_update_ts[0] = now

                        # Claude Code 卡片：上方显示 agent 进度，下方显示 CC 实时输出
                        prog_summary = ""
                        if steps_done:
                            prog_summary = f"**已完成 {len(steps_done)} 个工具** | "
                        code_block = "\n".join(claude_lines)
                        body_md = (
                            f"{prog_summary}**⚙️ Claude Code 执行中...**\n\n"
                            f"```\n{code_block}\n```"
                        )
                        _self._patch_card_sync(card_message_id, body_md, "Claude Code", "orange")
                        return

                    # ── 普通工具调用进度 ──────────────────────────
                    if evt_type == "tool_start":
                        name = evt.get("name", "unknown")
                        arguments = evt.get("arguments") or {}
                        desc = _fmt_args(name, arguments)
                        step_current[0] = {"name": name, "desc": desc}
                        if name == "claude_code":
                            in_claude_code[0] = True
                            claude_lines.clear()
                    elif evt_type == "tool_end":
                        name = evt.get("name", "") or (step_current[0].get("name", "") if step_current[0] else "")
                        result = evt.get("result", "") or ""
                        # 取结果首行作为摘要
                        result_summary = result.split("\n")[0][:80] if result else ""
                        cur = step_current[0] if step_current[0].get("name") == name else {"name": name, "desc": ""}
                        steps_done.append({"name": name, "desc": cur.get("desc", ""), "result": result_summary})
                        step_current[0] = {}
                        if name == "claude_code":
                            in_claude_code[0] = False
                    else:
                        # thinking 等事件：卡片已显示"思考中"，无需更新
                        return

                    # 节流：两次 patch 间隔不低于 1 秒
                    now = time.time()
                    if now - last_update_ts[0] < 1.0:
                        return
                    last_update_ts[0] = now

                    _build_normal_card()

                progress_callback = _on_progress

            metadata: dict[str, Any] = {
                "message_id": message_id,
                "chat_type": chat_type,
                "msg_type": msg_type,
            }
            if progress_callback is not None:
                metadata["progress_callback"] = progress_callback

            # /stop 命令：直接停止当前 session 的 agent/tool 调用，不入队
            if content.strip().lower() == "/stop" and self._agent:
                self._agent.cancel_current_request(channel="feishu", session_id=reply_to)
                await self.bus.publish_outbound(
                    OutboundMessage(channel="feishu", chat_id=reply_to, content="已发送停止指令。")
                )
                return

            # 启动子 Agent 进度监听，用于 spawn/claude_code 等后台任务的实时进度展示
            if reply_to not in self._subagent_watcher_tasks:
                watcher = asyncio.create_task(
                    self._watch_subagent_progress(reply_to, receive_id_type)
                )
                self._subagent_watcher_tasks[reply_to] = watcher

            await self._handle_message(
                sender_id=sender_id,
                chat_id=reply_to,
                content=content,
                media=media_paths,
                metadata=metadata,
            )

            # 子 agent 完成后通过 _announce_result 通知主 agent，主 agent 统一 publish_outbound 推送结果

        except Exception:
            logger.exception("Error processing Feishu message")

    # ── 消息解析 ───────────────────────────────────────────────

    @staticmethod
    def _parse_text_content(raw: str | None) -> str:
        """解析飞书 text 消息的 JSON content。"""
        if not raw:
            return ""
        try:
            return json.loads(raw).get("text", "")
        except (json.JSONDecodeError, TypeError):
            return raw

    async def _parse_post_content(
        self, message_id: str, raw: str | None
    ) -> tuple[str, list[str]]:
        """
        解析飞书 post（富文本）消息，返回 (纯文本, [图片路径列表])。

        Post 格式:
        {"zh_cn": {"title": "...", "content": [[{tag, text/image_key, ...}]]}}
        """
        if not raw:
            return "", []

        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw or "", []

        # 支持 zh_cn / en_us / 任意第一个 locale
        locale_data = body.get("zh_cn") or body.get("en_us")
        if not locale_data:
            first = next(iter(body.values()), None) if isinstance(body, dict) else None
            if isinstance(first, dict):
                locale_data = first
            else:
                return str(body), []

        title = locale_data.get("title", "")
        content_blocks: list[list[dict]] = locale_data.get("content", [])

        text_parts: list[str] = []
        image_paths: list[str] = []

        if title:
            text_parts.append(title)

        for paragraph in content_blocks:
            line_parts: list[str] = []
            for elem in paragraph:
                tag = elem.get("tag", "")
                if tag == "text":
                    line_parts.append(elem.get("text", ""))
                elif tag == "a":
                    href = elem.get("href", "")
                    link_text = elem.get("text", href)
                    line_parts.append(f"{link_text}({href})")
                elif tag == "at":
                    line_parts.append(f"@{elem.get('user_name', elem.get('user_id', ''))}")
                elif tag == "img":
                    image_key = elem.get("image_key", "")
                    if image_key:
                        path = await self._download_image_by_key(message_id, image_key)
                        if path:
                            image_paths.append(path)
                elif tag == "media":
                    line_parts.append("[视频]")
                elif tag == "code_block":
                    code_text = elem.get("text", "")
                    lang = elem.get("language", "")
                    line_parts.append(f"```{lang}\n{code_text}\n```")
            if line_parts:
                text_parts.append("".join(line_parts))

        return "\n".join(text_parts), image_paths

    # ── 图片下载 ───────────────────────────────────────────────

    async def _download_image_from_message(
        self, message_id: str, raw_content: str | None
    ) -> str | None:
        """从 image 消息中提取 image_key 并下载。"""
        if not raw_content:
            return None
        try:
            image_key = json.loads(raw_content).get("image_key", "")
        except (json.JSONDecodeError, TypeError):
            return None
        if not image_key:
            return None
        return await self._download_image_by_key(message_id, image_key)

    async def _download_image_by_key(
        self, message_id: str, image_key: str
    ) -> str | None:
        """
        通过飞书 API 下载图片资源。

        使用 im.v1.message_resource.get 接口：
        GET /open-apis/im/v1/messages/:message_id/resources/:file_key?type=image
        """
        if not self._client or not image_key:
            return None

        try:
            media_dir = _media_dir(self._workspace)
            media_dir.mkdir(parents=True, exist_ok=True)
            save_path = media_dir / f"feishu_{image_key[:20]}.png"

            if save_path.exists() and save_path.stat().st_size > 0:
                logger.debug(f"Feishu image cache hit: {save_path}")
                return str(save_path)

            request = GetMessageResourceRequest.builder() \
                .message_id(message_id) \
                .file_key(image_key) \
                .type("image") \
                .build()

            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._client.im.v1.message_resource.get, request
            )

            if not response.success():
                logger.warning(f"Feishu image download failed: code={response.code} msg={response.msg} (key={image_key})")
                return None

            # lark-oapi 返回 response.file (file-like) 或 response.data
            file_obj = getattr(response, "file", None)
            if file_obj is not None:
                if hasattr(file_obj, "read"):
                    data = file_obj.read()
                else:
                    data = file_obj
                with open(save_path, "wb") as f:
                    f.write(data)
                logger.info(f"Feishu image downloaded: {save_path} ({len(data)} bytes)")
                return str(save_path)

            logger.warning(f"Feishu image response has no file data (key={image_key})")
            return None

        except Exception as e:
            logger.warning(f"Feishu image download error (key={image_key}): {e}")
            return None

    async def _download_audio_from_message(
        self, message_id: str, message_content: str
    ) -> str | None:
        """
        从飞书消息中下载语音资源。

        语音消息 content 示例：{"file_key": "file_xxx", "duration": 12345}
        """
        try:
            content = json.loads(message_content) if message_content else {}
            logger.debug(f"Feishu audio message content: {content}")
            file_key = content.get("file_key")
            if not file_key:
                logger.warning(f"Feishu audio message has no file_key, content: {content}")
                return None
            return await self._download_audio_by_key(message_id, file_key)
        except json.JSONDecodeError as e:
            logger.warning(f"Feishu audio message JSON parse error: {e}, content: {message_content[:200]}")
            return None

    async def _download_audio_by_key(
        self, message_id: str, file_key: str
    ) -> str | None:
        """
        通过飞书 API 下载语音资源。

        使用 im.v1.message_resource.get 接口：
        GET /open-apis/im/v1/messages/:message_id/resources/:file_key?type=file
        注：飞书语音消息以 file 类型存储，而非 audio 类型
        """
        if not self._client or not file_key:
            return None

        try:
            media_dir = _media_dir(self._workspace)
            media_dir.mkdir(parents=True, exist_ok=True)
            # 飞书语音通常是 opus 格式，但保存为 .ogg 或 .mp3
            save_path = media_dir / f"feishu_audio_{file_key[:20]}.ogg"

            if save_path.exists() and save_path.stat().st_size > 0:
                logger.debug(f"Feishu audio cache hit: {save_path}")
                return str(save_path)

            request = GetMessageResourceRequest.builder() \
                .message_id(message_id) \
                .file_key(file_key) \
                .type("file") \
                .build()

            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self._client.im.v1.message_resource.get, request
            )

            if not response.success():
                logger.warning(
                    f"Feishu audio download failed: code={response.code} msg={response.msg} (key={file_key})"
                )
                return None

            # lark-oapi 返回 response.file (file-like) 或 response.data
            file_obj = getattr(response, "file", None)
            if file_obj is not None:
                if hasattr(file_obj, "read"):
                    data = file_obj.read()
                else:
                    data = file_obj
                with open(save_path, "wb") as f:
                    f.write(data)
                logger.info(f"Feishu audio downloaded: {save_path} ({len(data)} bytes)")
                return str(save_path)

            logger.warning(f"Feishu audio response has no file data (key={file_key})")
            return None

        except Exception as e:
            logger.warning(f"Feishu audio download error (key={file_key}): {e}")
            return None

    # ── Reaction ──────────────────────────────────────────────

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
        try:
            request = CreateMessageReactionRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                ).build()

            response = self._client.im.v1.message_reaction.create(request)
            if not response.success():
                logger.warning(f"Failed to add reaction: code={response.code}, msg={response.msg}")
        except Exception as e:
            logger.warning(f"Error adding reaction: {e}")

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        if not self._client or not Emoji:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)
