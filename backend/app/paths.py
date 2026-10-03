"""运行时文件布局 —— 数据根目录与它下面的子目录只在这里定义一次。

根目录取自 settings.data_dir（容器内是挂载点 /app/data，宿主机上是 .env 里的宿主路径，
由 compose 把容器内那份钉死）。子目录名以前散落在十来个模块里各写各的（Path("data/worlds") 这种）：
进程从别的 cwd 启动就会在别处再写一份数据（backend/data/worlds 就是这么出现的），而插件与商城
那几处读的是宿主视角的 DATA_DIR，在容器里根本不存在。要挪数据根只改一处，要加目录也只在这里加。

路径在 import 时解析一次；测试由 conftest 把 DATA_DIR 指向临时目录，早于任何 app 模块的导入。
"""
from pathlib import Path

from app.config import settings

DATA_DIR = Path(settings.data_dir)

# 每个 AI 的文件空间：附件、记忆、脚本沙箱都在它下面，file_* 工具与 run_script 共用这一条边界
AGENTS_DIR = DATA_DIR / "agents"
# 世界：{id}/ 是代码区与沙箱工作目录，{id}/data/ 是数据区
WORLDS_DIR = DATA_DIR / "worlds"
# 设计侧技能库（造物主全局共享；与「世界内颁布的 skills/」不是一回事）
AI_SKILLS_DIR = DATA_DIR / "world_ai_skills"
# 用户安装的插件与安装包仓库（内置插件在 backend/plugins，不走这里）
PLUGINS_DIR = DATA_DIR / "plugins"
PLUGIN_PACKAGES_DIR = DATA_DIR / "plugin_packages"
# 世界商城：商品包
MARKET_DIR = DATA_DIR / "market"
# 备份、全局共享记忆、主题投票
BACKUPS_DIR = DATA_DIR / "backups"
SHARED_MEMORIES_DIR = DATA_DIR / "shared_memories"
THEME_VOTES_FILE = DATA_DIR / "theme_votes.json"
THEME_VOTES_R2_FILE = DATA_DIR / "theme_votes_r2.json"
