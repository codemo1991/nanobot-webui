# 统一图片处理路径实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将图片处理统一为单一路径：直接 base64 传给 LLM，失败时 fallback 到 subagent。

**Architecture:**
- 移除 `_is_vision_model()` 预判逻辑
- 所有图片直接走 base64 路径（已有 `ContextBuilder._build_user_content()` 实现）
- LLM 调用失败时捕获图片相关错误，自动 fallback 调用 `subagent.run_vision_analysis()`
- 用图片文字描述替换消息中的 base64 图片后重试

**Tech Stack:** Python 3.11+, asyncio, nanobot agent framework

---

## 文件清单

- `nanobot/agent/loop.py` — 主改动文件

---

## Task 1: 添加图片错误检测和消息替换辅助方法

**Files:**
- Modify: `nanobot/agent/loop.py` (在 `_is_audio_file` 方法附近添加新方法)

**前置条件:** 无

- [ ] **Step 1: 添加辅助方法**

在 `_is_audio_file` 方法后添加两个新方法：

```python
def _is_image_unsupported_error(self, error: Exception) -> bool:
    """检测错误是否是图片不支持导致的。"""
    error_str = str(error).lower()
    unsupported_keywords = [
        "image", "vision", "multimodal", "modality",
        "does not support images", "not support images",
        "vision capability", "content type", "invalid content type",
    ]
    # 同时检查 HTTP 状态码相关的错误
    if hasattr(error, "status_code"):
        if error.status_code == 400:
            return True
    return any(kw in error_str for kw in unsupported_keywords)

def _replace_images_with_description(self, messages: list[dict[str, Any]], description: str) -> list[dict[str, Any]]:
    """将消息列表中的 base64 图片替换为文字描述。"""
    import copy
    new_messages = copy.deepcopy(messages)
    for msg in new_messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if url.startswith("data:image/"):
                        # 跳过 base64 图片部分
                        continue
                new_content.append(part)
            # 如果所有内容都被移除了，添加文字描述
            if not new_content:
                new_content.append({"type": "text", "text": description})
            else:
                # 最后一个文本部分追加描述
                for i in range(len(new_content) - 1, -1, -1):
                    if new_content[i].get("type") == "text":
                        new_content[i]["text"] = f"{new_content[i].get('text', '')}\n\n{description}".strip()
                        break
                else:
                    new_content.append({"type": "text", "text": description})
            msg["content"] = new_content
    return new_messages
```

- [ ] **Step 2: 运行测试验证语法**

Run: `cd E:/workSpace/nanobot-webui && python -m py_compile nanobot/agent/loop.py`
Expected: 无输出（编译成功）

- [ ] **Step 3: 提交**

```bash
git add nanobot/agent/loop.py
git commit -m "feat: add image error detection and message replacement helpers"
```

---

## Task 2: 移除 vision 预判逻辑

**Files:**
- Modify: `nanobot/agent/loop.py:2208-2239` (移除图片预判分支)
- Modify: `nanobot/agent/loop.py:2991-3030` (移除另一个 vision 兜底分支)

**前置条件:** Task 1 完成

### Part A: 移除第一个图片预判分支

- [ ] **Step 1: 定位并修改图片处理代码**

找到 `if image_files:` 分支（约 2208 行），将整个 `if image_files:` 块（包括 `_is_vision_model` 判断和 subagent 调用）替换为简化的日志：

```python
        # 统一图片处理：所有图片直接通过 ContextBuilder 转为 base64 传给 LLM
        # 如果 LLM 不支持图片，会在调用时抛出异常，由 fallback 逻辑处理
        if image_files:
            logger.info(f"[Image] Found {len(image_files)} images, passing directly to LLM")
```

### Part B: 移除第二个 vision 兜底分支

- [ ] **Step 2: 定位并移除 vision 兜底逻辑**

找到约 2991-3030 行的兜底逻辑：

```python
        # 兜底逻辑：如果主模型不支持视觉且配置了 subagent_model，但模型没有 spawn vision 子agent
        # 则使用 inline image recognition 作为兜底（只对图片）
        if image_files and not self._is_vision_model(self.model) and self.subagent_model and self.subagent_model != self.model:
            # ... 整个 if 块删除 ...
```

删除整个 if 块（包括所有缩进的 subagent 调用代码），替换为：

```python
        # 统一图片处理：fallback 逻辑已在 LLM 调用异常处处理
        # 此处不再需要单独的 vision 兜底逻辑
```

### Part C: 删除 `_is_vision_model` 方法

- [ ] **Step 3: 删除 `_is_vision_model` 方法**

删除 `_is_vision_model` 方法（约 1574-1580 行）：

```python
# 删除以下方法
def _is_vision_model(self, model: str) -> bool:
    """Check if a model supports vision/images."""
    if not model:
        return False
    model_lower = model.lower()
    vision_keywords = ["vision", "vl", "qwen-vl", "gpt-4v", "gpt-4o", "claude-3-opus", "claude-3-sonnet", "claude-3-5", "claude-4"]
    return any(kw in model_lower for kw in vision_keywords)
```

- [ ] **Step 4: 运行测试验证语法**

Run: `cd E:/workSpace/nanobot-webui && python -m py_compile nanobot/agent/loop.py`
Expected: 无输出（编译成功）

- [ ] **Step 5: 提交**

```bash
git add nanobot/agent/loop.py
git commit -m "refactor: remove vision pre-check logic for unified image handling"
```

---

## Task 3: 添加 LLM 调用 fallback 逻辑

**Files:**
- Modify: `nanobot/agent/loop.py` (在 LLM 调用处添加 try-catch)

**前置条件:** Task 2 完成

- [ ] **Step 1: 找到 LLM 调用位置**

在 `_run_agent_loop` 方法中，找到 `async with span("llm.inference"` 块（约 2371 行），这是 LLM 调用的入口。

- [ ] **Step 2: 在 LLM 调用外层添加 fallback 逻辑**

将现有的 LLM 调用包装在 try-catch 中，添加图片 fallback：

```python
            fallback_attempted = False
            image_files = [m for m in (msg.media or []) if self._is_image_file(m)]
            original_messages = messages

            async with span("llm.inference", attrs={"model": self.model}) as llm_span:
                try:
                    # ... 现有的 LLM 调用逻辑 (保持不变) ...
                    llm_task = asyncio.create_task(
                        self._call_llm_with_failover(
                            messages=messages,
                            tools=selected_tools,
                        )
                    )
                    # ... 现有的取消检查和超时逻辑 (保持不变) ...
                    response, _ = await llm_task
                except Exception as llm_error:
                    llm_span.set_attr("exit_reason", "error")
                    llm_span.end(status="error")

                    # 检查是否是图片不支持错误且尚未尝试过 fallback
                    if image_files and not fallback_attempted and self._is_image_unsupported_error(llm_error):
                        logger.warning(f"[ImageFallback] LLM 不支持图片，尝试 fallback: {str(llm_error)[:100]}")
                        fallback_attempted = True

                        # 调用 vision subagent 分析图片
                        session_key = getattr(msg, "session_key", f"{msg.channel}:{msg.chat_id}")
                        sk_channel, sk_chat_id = (session_key.split(":", 1) + [session_key])[:2]
                        task_text = msg.content.strip() or "请详细分析这些图片的内容。"
                        img_desc = await self.subagents.run_vision_analysis(
                            task=task_text, media=image_files,
                            origin_channel=sk_channel, origin_chat_id=sk_chat_id,
                        )

                        # 用图片描述替换 base64 图片
                        messages = self._replace_images_with_description(original_messages, img_desc or "用户发送了图片，但无法识别内容。")
                        logger.info("[ImageFallback] 使用图片描述重试 LLM 调用")

                        # 重试 LLM 调用
                        async with span("llm.inference", attrs={"model": self.model, "fallback": True}) as retry_span:
                            try:
                                llm_task = asyncio.create_task(
                                    self._call_llm_with_failover(
                                        messages=messages,
                                        tools=selected_tools,
                                    )
                                )
                                loop_start_llm = time.monotonic()
                                while not llm_task.done():
                                    elapsed_llm = time.monotonic() - loop_start_llm
                                    remaining = _LLM_CALL_TIMEOUT - elapsed_llm
                                    if remaining <= 0:
                                        llm_task.cancel()
                                        try:
                                            await llm_task
                                        except asyncio.CancelledError:
                                            pass
                                        raise asyncio.TimeoutError()
                                    wait_time = min(_CANCEL_CHECK_INTERVAL, remaining)
                                    done, _ = await asyncio.wait(
                                        [llm_task],
                                        timeout=wait_time,
                                        return_when=asyncio.FIRST_COMPLETED,
                                    )
                                    if llm_task in done:
                                        break
                                    cancelled = (sk in self._cancelled_sessions) or self._cancel_event.is_set()
                                    if cancelled:
                                        llm_task.cancel()
                                        try:
                                            await llm_task
                                        except asyncio.CancelledError:
                                            pass
                                        if sk in self._cancelled_sessions:
                                            self._cancelled_sessions.discard(sk)
                                        else:
                                            self._cancel_event.clear()
                                        raise asyncio.CancelledError("Request cancelled by user")
                                response, _ = await llm_task
                            except Exception as retry_error:
                                retry_span.set_attr("exit_reason", "error")
                                retry_span.end(status="error")
                                raise
                            else:
                                retry_span.set_attr("finish_reason", getattr(response, "finish_reason", None) or "")
                                retry_span.set_attr("fallback_used", True)
                    else:
                        raise
```

**注意：** 需要在方法开头添加 `fallback_attempted = False` 变量。

- [ ] **Step 3: 运行测试验证语法**

Run: `cd E:/workSpace/nanobot-webui && python -m py_compile nanobot/agent/loop.py`
Expected: 无输出（编译成功）

- [ ] **Step 4: 提交**

```bash
git add nanobot/agent/loop.py
git commit -m "feat: add image fallback to subagent on LLM error"
```

---

## Task 4: 验证改动完整性

**Files:**
- None (仅验证)

**前置条件:** Tasks 1-3 完成

- [ ] **Step 1: 运行完整的语法检查**

Run: `cd E:/workSpace/nanobot-webui && python -m py_compile nanobot/agent/loop.py nanobot/agent/context.py nanobot/agent/subagent.py`
Expected: 无输出（编译成功）

- [ ] **Step 2: 确认设计文档中的改动点都已实现**

对照 `docs/superpowers/specs/2026-04-07-unify-image-handling-design.md` 检查：
- [x] `_is_vision_model()` 已删除
- [x] 图片场景下的 subagent 预判断分支已删除
- [x] LLM 调用添加了异常捕获
- [x] 图片不支持时自动 fallback 到 subagent

- [ ] **Step 3: 运行 git diff 查看改动摘要**

Run: `git diff --stat HEAD~3 HEAD`
Expected: 显示 nanobot/agent/loop.py 的改动行数

- [ ] **Step 4: 最终提交**

```bash
git add -A
git commit -m "feat: unify image handling - pass base64 directly, fallback to subagent on error"
```

---

## 异常处理说明

Fallback 触发条件：
1. 检测到 `image_files`（有图片）
2. 尚未尝试过 fallback（`fallback_attempted == False`）
3. LLM 调用抛出异常
4. 异常信息包含图片相关关键词（如 "image", "vision", "multimodal", "does not support images" 等）

Fallback 行为：
1. 调用 `subagent.run_vision_analysis()` 获取图片文字描述
2. 用描述替换消息中的 base64 图片
3. 重新执行 LLM 调用
4. 如果重试仍然失败，抛出异常向上传递
