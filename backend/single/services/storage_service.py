import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from core.config import settings


class StorageService(ABC):
    """存储服务抽象基类"""
    
    @abstractmethod
    async def save_file(self, file_path: str, key: str) -> str:
        """保存文件，返回访问路径"""
        pass
    
    @abstractmethod
    async def save_upload(self, file_content: bytes, filename: str, task_id: str) -> str:
        """保存上传的文件"""
        pass
    
    @abstractmethod
    async def get_file_path(self, key: str) -> str:
        """获取文件的完整路径"""
        pass
    
    @abstractmethod
    async def get_file_url(self, key: str) -> str:
        """获取文件的访问URL"""
        pass
    
    @abstractmethod
    async def delete_file(self, key: str) -> bool:
        """删除文件"""
        pass


class LocalStorageService(StorageService):
    """本地文件存储实现（MVP阶段使用）"""
    
    def __init__(self):
        self.base_dir = Path(settings.STORAGE_DIR)
        self.uploads_dir = self.base_dir / "uploads"
        self.outputs_dir = self.base_dir / "outputs"
        
        # 确保目录存在
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
    
    async def save_file(self, file_path: str, key: str) -> str:
        """保存文件到本地，返回相对路径"""
        dest_path = self.base_dir / key
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, dest_path)
        return str(key)
    
    async def save_upload(self, file_content: bytes, filename: str, task_id: str) -> str:
        """保存上传的文件"""
        key = f"uploads/{task_id}/{filename}"
        dest_path = self.base_dir / key
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(file_content)
        return key
    
    async def save_output(self, file_content: bytes, filename: str, task_id: str) -> str:
        """保存输出文件"""
        key = f"outputs/{task_id}/{filename}"
        dest_path = self.base_dir / key
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(file_content)
        return key
    
    async def get_file_path(self, key: str) -> str:
        """获取文件的完整本地路径"""
        return str(self.base_dir / key)
    
    async def get_file_url(self, key: str) -> str:
        """获取文件的访问URL（供前端下载）"""
        return f"/files/{key}"
    
    async def delete_file(self, key: str) -> bool:
        """删除文件"""
        file_path = self.base_dir / key
        if file_path.exists():
            file_path.unlink()
            return True
        return False
    
    async def read_file(self, key: str) -> Optional[bytes]:
        """读取文件内容"""
        file_path = self.base_dir / key
        if file_path.exists():
            return file_path.read_bytes()
        return None


def get_storage_service() -> StorageService:
    """工厂函数，根据配置返回对应的存储服务实现"""
    if settings.STORAGE_TYPE == "local":
        return LocalStorageService()
    # 后续可扩展 OSS 实现
    # elif settings.STORAGE_TYPE == "oss":
    #     return OSSStorageService()
    else:
        return LocalStorageService()
