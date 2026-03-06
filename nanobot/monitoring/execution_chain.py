"""Execution chain monitoring for tracking agent, subagent, and tool executions."""

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class ExecutionChain:
    """执行链路"""
    chain_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    session_key: str = ""
    channel: str = ""
    chat_id: str = ""
    root_prompt: str = ""
    status: str = "running"
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    # 内存中的节点缓存
    _nodes: dict = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def create_node(
        self,
        node_type: str,
        name: str,
        parent_node_id: str = None,
        arguments: dict = None
    ) -> "ExecutionNode":
        """创建执行节点"""
        node = ExecutionNode(
            chain_id=self.chain_id,
            parent_node_id=parent_node_id,
            node_type=node_type,
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False) if arguments else None
        )
        with self._lock:
            self._nodes[node.node_id] = node
        return node

    def complete_node(self, node_id: str, result: str = None, error: str = None):
        """完成节点执行"""
        with self._lock:
            if node_id in self._nodes:
                node = self._nodes[node_id]
                node.finished_at = datetime.now()
                node.duration_ms = int((node.finished_at - node.started_at).total_seconds() * 1000)
                node.result = result[:5000] if result else None  # 截断过长结果
                node.status = "completed" if not error else "failed"
                node.error_message = error
                return node
        return None

    def finish(self, status: str = "completed"):
        """结束链路"""
        self.finished_at = datetime.now()
        self.duration_ms = int((self.finished_at - self.started_at).total_seconds() * 1000)
        self.status = status

    def get_nodes(self) -> list["ExecutionNode"]:
        """获取所有节点"""
        with self._lock:
            return list(self._nodes.values())


@dataclass
class ExecutionNode:
    """执行节点"""
    node_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    chain_id: str = ""
    parent_node_id: Optional[str] = None
    node_type: str = ""  # agent / tool / subagent
    name: str = ""
    arguments: Optional[str] = None
    result: Optional[str] = None
    status: str = "running"
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None


class ExecutionChainMonitor:
    """执行链路监控器"""

    _instance = None
    _lock = threading.Lock()

    def __init__(self, db_path: Path = None):
        self._db_path = db_path
        self._current_chain: Optional[ExecutionChain] = None
        self._chains: dict[str, ExecutionChain] = {}
        self._repo = None
        self._async_save_queue: list = []
        self._save_lock = threading.Lock()

    @classmethod
    def get_instance(cls, db_path: Path = None) -> "ExecutionChainMonitor":
        """获取单例实例"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置实例（主要用于测试）"""
        with cls._lock:
            cls._instance = None

    def set_repository(self, repo):
        """设置数据库访问仓库"""
        self._repo = repo

    def start_chain(
        self,
        session_key: str,
        channel: str,
        chat_id: str,
        root_prompt: str
    ) -> ExecutionChain:
        """开始新的执行链路"""
        chain = ExecutionChain(
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            root_prompt=root_prompt[:500]  # 截断长内容
        )
        self._current_chain = chain
        self._chains[chain.chain_id] = chain

        # 异步写入数据库
        self._save_chain_async(chain, is_new=True)

        logger.info(f"[ExecutionChain] Started chain: {chain.chain_id}, session: {session_key}")
        return chain

    def get_current_chain(self) -> Optional[ExecutionChain]:
        """获取当前执行链路"""
        return self._current_chain

    def get_chain(self, chain_id: str) -> Optional[ExecutionChain]:
        """根据 ID 获取链路"""
        return self._chains.get(chain_id)

    def end_chain(self, status: str = "completed"):
        """结束当前执行链路"""
        if self._current_chain:
            self._current_chain.finish(status)
            self._save_chain_async(self._current_chain, is_new=False)
            logger.info(f"[ExecutionChain] Ended chain: {self._current_chain.chain_id}, status: {status}")
            self._current_chain = None

    def _save_chain_async(self, chain: ExecutionChain, is_new: bool = False):
        """异步保存到数据库"""
        if not self._repo:
            return

        def _save():
            try:
                if is_new:
                    self._repo.create_chain(chain)
                else:
                    self._repo.update_chain(chain)
                # 保存所有节点
                for node in chain.get_nodes():
                    self._repo.upsert_node(node)
            except Exception as e:
                logger.error(f"[ExecutionChain] Failed to save chain: {e}")

        # 在后台线程保存
        thread = threading.Thread(target=_save, daemon=True)
        thread.start()

    def query_chains(
        self,
        session_key: str = None,
        status: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100
    ) -> list[dict]:
        """查询链路列表"""
        if not self._repo:
            return []

        try:
            rows = self._repo.query_chains(
                session_key=session_key,
                status=status,
                start_time=start_time,
                end_time=end_time,
                limit=limit
            )
            return rows
        except Exception as e:
            logger.error(f"[ExecutionChain] Failed to query chains: {e}")
            return []

    def get_chain_detail(self, chain_id: str) -> Optional[dict]:
        """获取链路详情（包含调用树）"""
        if not self._repo:
            return None

        try:
            chain_data = self._repo.get_chain_by_id(chain_id)
            if not chain_data:
                return None

            nodes = self._repo.get_nodes_by_chain(chain_id)
            chain_data["nodes"] = nodes
            return chain_data
        except Exception as e:
            logger.error(f"[ExecutionChain] Failed to get chain detail: {e}")
            return None
