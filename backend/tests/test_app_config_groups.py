"""配置组（system_settings 的 JSONB 列）：DB 覆盖要持久，多个组不能互相吃

机制见 app/services/infrastructure/app_config_service.py：启动逐组加载、保存逐组写回，
DB 覆盖经 db_config_source 灌进 Settings（重启后由 bootstrap 重新加载）。
"""
import pytest

pytestmark = pytest.mark.anyio


async def _repo(db):
    from app.repositories.infra_repo import SQLAlchemyInfraRepository
    return SQLAlchemyInfraRepository(db)


async def test_saving_one_group_does_not_wipe_another(migrated_db):
    """逐组保存不能把先前那组的覆盖吃掉（曾经是整体替换缓存）"""
    from sqlalchemy import select

    from app.config import get_effective_upload_max_size_mb
    from app.database import async_session
    from app.db_config_source import get_db_override
    from app.models.system_settings import SystemSettings
    from app.services.infrastructure.app_config_service import clear_group_config, save_group_config

    async with async_session() as db:
        repo = await _repo(db)
        await save_group_config(repo, "embedding", {"embedding_model": "probe-model"})
        await save_group_config(repo, "runtime", {"upload_max_size_mb": 64})

        assert get_db_override("embedding_model") == "probe-model", "先保存的组被后一组吃掉了"
        assert get_db_override("upload_max_size_mb") == 64
        assert get_effective_upload_max_size_mb() == 64, "热生效：settings 重建后要读得到"

        # 落库了（重启就是从这一列读回来）
        row = (await db.execute(select(SystemSettings).where(SystemSettings.id == 1))).scalar_one()
        assert row.runtime_config["upload_max_size_mb"] == 64

        # 恢复某一组默认：只清本组，别的组不受影响
        await clear_group_config(repo, "runtime")
        assert get_db_override("upload_max_size_mb") is None
        assert get_db_override("embedding_model") == "probe-model", "清 runtime 不该动 embedding"

        await clear_group_config(repo, "embedding")   # 复原，别把全局状态留给后面的用例


async def test_runtime_overrides_survive_a_reload(migrated_db):
    """模拟重启：清内存缓存后重新加载，值从 DB 回来（这是「不再丢」的实质）"""
    from app.config import get_effective_upload_max_size_mb
    from app.database import async_session
    from app.db_config_source import clear_db_overrides
    from app.services.infrastructure.app_config_service import clear_group_config, load_group_config, save_group_config

    async with async_session() as db:
        repo = await _repo(db)
        await save_group_config(repo, "runtime", {"upload_max_size_mb": 77})
        assert get_effective_upload_max_size_mb() == 77

        clear_db_overrides()                       # 相当于进程重启：内存里的覆盖没了
        await load_group_config(db, "runtime")     # 启动时的 load_all_configs
        assert get_effective_upload_max_size_mb() == 77, "重启后要能从 DB 读回来"

        await clear_group_config(repo, "runtime")