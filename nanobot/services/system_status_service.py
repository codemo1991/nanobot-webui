"""System status service for collecting and aggregating system status data."""

import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.skills import SkillsLoader
from nanobot.session.manager import SessionManager
from nanobot.storage.status_repository import StatusRepository


class SystemStatusService:
    """系统状态服务，负责收集和聚合系统状态数据。"""
    
    def __init__(
        self,
        status_repo: StatusRepository,
        session_manager: SessionManager,
        workspace: Path
    ):
        """
        初始化系统状态服务。
        
        Args:
            status_repo: 状态数据仓库
            session_manager: 会话管理器
            workspace: 工作空间路径
        """
        self.status_repo = status_repo
        self.session_manager = session_manager
        self.workspace = workspace
        self.skills_loader = SkillsLoader(workspace)
    
    def initialize(self) -> None:
        """初始化系统状态（记录启动时间）。"""
        try:
            current_time = time.time()
            self.status_repo.set_start_time(current_time)
            logger.info(f"System status initialized with start_time: {current_time}")
        except Exception as e:
            logger.exception("Failed to initialize system status")
    
    def get_uptime(self) -> int:
        """
        获取系统运行时长。
        
        Returns:
            运行时长（秒）
        """
        try:
            start_time = self.status_repo.get_start_time()
            if start_time is None:
                logger.warning("Start time not found, returning 0")
                return 0
            
            current_time = time.time()
            uptime = int(current_time - start_time)
            return max(0, uptime)  # Ensure non-negative
        except Exception as e:
            logger.exception("Failed to get uptime")
            return 0
    
    def get_session_count(self) -> int:
        """
        获取会话数量。
        
        Returns:
            会话总数
        """
        try:
            sessions = self.session_manager.list_sessions()
            count = len(sessions)
            logger.debug(f"Session count: {count}")
            return count
        except Exception as e:
            logger.exception("Failed to get session count")
            return 0
    
    def get_skills_info(self) -> list[dict[str, Any]]:
        """
        获取已安装的 Skills 信息。
        
        Returns:
            Skills 信息列表，每个元素包含 name, version, description
        """
        try:
            all_skills = self.skills_loader.list_skills(filter_unavailable=False)
            skills_info = []
            
            for skill in all_skills:
                name = skill["name"]
                metadata = self.skills_loader.get_skill_metadata(name) or {}
                
                skills_info.append({
                    "name": name,
                    "version": metadata.get("version", "unknown"),
                    "description": metadata.get("description", name),
                    "source": skill.get("source", "unknown")
                })
            
            logger.debug(f"Skills info retrieved: {len(skills_info)} skills")
            return skills_info
        except Exception as e:
            logger.exception("Failed to get skills info")
            return []
    
    def get_status(self) -> dict[str, Any]:
        """
        获取完整的系统状态信息。
        
        Returns:
            系统状态字典，包含 uptime, sessions, skills
        """
        try:
            uptime = self.get_uptime()
            session_count = self.get_session_count()
            skills_info = self.get_skills_info()
            
            status = {
                "uptime": uptime,
                "sessions": session_count,
                "skills": len(skills_info),
                "skills_list": skills_info
            }
            
            logger.debug(f"System status retrieved: {status}")
            return status
        except Exception as e:
            logger.exception("Failed to get system status")
            # Return default values on error
            return {
                "uptime": 0,
                "sessions": 0,
                "skills": 0,
                "skills_list": []
            }
