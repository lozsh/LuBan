import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm
import numpy as np
import re

# ── 颜色规范（禁止自定义颜色，只用这套）──
BLUE        = '#2563EB'   # 主色：输入层、主流程
PURPLE      = '#7C3AED'   # 辅色：模型组件、LLM核心
GREEN       = '#059669'   # 正面/输出：成功、输出层
ORANGE      = '#D97706'   # 中间态：警告、次要流程
GRAY        = '#6B7280'   # 说明文字、次要信息
RED         = '#DC2626'   # 错误、危险、阻断

LIGHT_BLUE   = '#EFF6FF'
LIGHT_PURPLE = '#F5F3FF'
LIGHT_GREEN  = '#ECFDF5'
LIGHT_ORANGE = '#FFFBEB'
LIGHT_GRAY   = '#F9FAFB'
LIGHT_RED    = '#FEF2F2'

# ── 字体初始化（自动验证，找不到中文字体会警告）──
def _setup_fonts():
    available = {f.name for f in fm.fontManager.ttflist}
    preferred = ['Arial Unicode MS', 'PingFang SC', 'SimHei']
    found = [f for f in preferred if f in available]
    if not found:
        print("WARNING: 未找到中文字体，中文可能显示为方块。请安装 PingFang SC 或 SimHei。")
    plt.rcParams['font.sans-serif'] = found + ['DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    plt.rcParams['font.size'] = 11

_setup_fonts()

# ── emoji 过滤（所有上图文字必须套此函数）──
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002700-\U000027BF"
    "\U0000FE00-\U0000FE0F"
    "\U00002600-\U000026FF"
    "]+", flags=re.UNICODE
)

def safe_text(s: str) -> str:
    """去掉 emoji，防止渲染为方块。所有上图文字必须套此函数。"""
    return _EMOJI_RE.sub('', s).strip()

# ── 带标签箭头（标签颜色强制 #374151）──
def draw_labeled_arrow(ax, x1, y1, x2, y2, label='', rad=0, color='#374151'):
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle='->', color=color, lw=1.5,
                                connectionstyle=f'arc3,rad={rad}'))
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx, my, safe_text(label), ha='center', va='bottom',
                fontsize=9, color='#374151',
                bbox=dict(facecolor='white', edgecolor='none', alpha=0.8, pad=2))

# ── 弧形箭头 rad 自动计算（不用记正负）──
def arc_rad(bend='right', strength=0.3) -> float:
    """bend='right' 向右弯（rad 负），bend='left' 向左弯（rad 正）"""
    return -strength if bend == 'right' else strength

# ── 保存规范（禁止修改 dpi 和 facecolor）──
# plt.savefig('images/chapter-XX/xxx.png', dpi=150, bbox_inches='tight', facecolor='white')
# plt.close()
# ── Layout design ──
# Canvas=(18,12), extent x=0..28,y=0..22. Same grid/style as system architecture.
# Labels x=.65..2.9; main x=3.3..22.2; delivery x=23.2..27.35.
# Clients y=18.6..20.3; frontend y=15.5..17.8 (gap .8).
# API y=12.8..14.7 (gap .8); execution y=9.3..12.0 (gap .8).
# Adapters y=6.4..8.5 (gap .8); persistence y=3.3..5.6 (gap .8).
# External resources y=.9..2.5 (gap .8); delivery y=3.3..17.8.
# No dependency spiderweb or invented distributed components; nested tech modules.
from pathlib import Path
import matplotlib
matplotlib.use('Agg')

# Restrained enterprise architecture: blue/gray, rectangular nested modules.
# Text is checked against its own cell; peer cells may not overlap.
TEXTS = []
CELLS = []
def box(x, y, w, h, fill='white', edge=GRAY, lw=0.85, dashed=False):
    ax.add_patch(mpatches.Rectangle((x,y),w,h,facecolor=fill,edgecolor=edge,
                                  linewidth=lw,linestyle='--' if dashed else '-'))

def label(x,y,w,h,text,size=12,bold=False,color=GRAY):
    t=ax.text(x+w/2,y+h/2,safe_text(text),ha='center',va='center',
              fontsize=size,fontweight='bold' if bold else 'normal',
              color=color,linespacing=1.45)
    TEXTS.append((t,(x,y,w,h)))

def cell(x,y,w,h,text,size=12,fill='white',accent=False):
    box(x,y,w,h,fill,BLUE if accent else GRAY,0.85)
    label(x,y,w,h,text,size,accent,BLUE if accent else GRAY)
    CELLS.append((x,y,w,h,text))

def layer(y,h,title,w=18.9):
    box(.65,y,2.25,h,LIGHT_BLUE,BLUE)
    label(.65,y,2.25,h,title,13,True,BLUE)
    box(3.3,y,w,h,LIGHT_GRAY,GRAY)

def group(x,y,w,h,title,items):
    box(x,y,w,h,'white',BLUE)
    label(x,y+h-.62,w,.58,title,13,True,BLUE)
    count=len(items);gap=.16;inner_y=y+.20;inner_h=h-.96
    cell_h=(inner_h-gap*(count-1))/count
    for i,text in enumerate(items):
        cell(x+.18,inner_y+(count-1-i)*(cell_h+gap),w-.36,cell_h,text,12,LIGHT_BLUE)

def chips(x,y,w,h,items,size=12):
    gap=.25;cw=(w-gap*(len(items)-1))/len(items)
    for i,text in enumerate(items):cell(x+i*(cw+gap),y,cw,h,text,size)

def validate():
    fig.canvas.draw()
    failures = []
    for text,(x,y,w,h) in TEXTS:
        bb=text.get_window_extent().transformed(ax.transData.inverted())
        if not (x+.03<=bb.x0 and bb.x1<=x+w-.03): failures.append((text.get_text(),'horizontal',str(bb),(x,y,w,h)))
        if not (y+.025<=bb.y0 and bb.y1<=y+h-.025): failures.append((text.get_text(),'vertical',str(bb),(x,y,w,h)))
    assert not failures, failures
    for i,a in enumerate(CELLS):
        for b in CELLS[i+1:]:
            overlap_x=min(a[0]+a[2],b[0]+b[2])-max(a[0],b[0])
            overlap_y=min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1])
            assert overlap_x<=.01 or overlap_y<=.01,(a[4],b[4],'overlap')

fig,ax=plt.subplots(figsize=(18,12))
ax.set_xlim(0,28);ax.set_ylim(0,22);ax.axis('off')

label(.65,20.70,26.7,1.02,'LubanAgent 技术架构图',24,True,BLUE)
label(.65,20.26,26.7,.58,'前端、Python 后端、数据与工程交付',13)
layer(18.6,1.7,'访问端',24.05)
chips(3.55,18.94,23.55,1.0,['浏览器 · 工作台','业务系统 · HTTP 客户端','命令行 · Typer CLI'],13)

layer(15.5,2.3,'前端工作台')
chips(3.55,16.26,18.4,1.08,['React 18\nTypeScript','Tailwind CSS\nRadix UI','React Router\nTanStack Query','OpenAPI 类型\nopenapi-fetch'],12)
label(3.55,15.65,18.4,.53,'工作台静态资源由 Python 服务提供；Node.js 用于开发与构建',11.5)

layer(12.8,1.9,'接口服务')
chips(3.55,13.26,18.4,1.00,['FastAPI\nREST · SSE · OpenAPI','Pydantic\n参数校验 · 配置','Uvicorn\nASGI 服务'],12)

layer(9.3,2.7,'执行引擎')
group(3.55,9.54,5.93,2.22,'LangGraph',['单 Agent · 代码流程','多 Agent 委派 · 图执行'])
group(9.73,9.54,5.93,2.22,'LangChain',['消息与模型接口','工具调用适配'])
group(15.91,9.54,6.04,2.22,'Luban Runtime',['上下文 · 检查点','取消 · 超时 · 预算'])

layer(6.4,2.1,'能力适配')
chips(3.55,6.86,18.4,1.18,['模型工厂\nlangchain-openai','工具与协议\nPython Tool · MCP SDK','Skill 执行\n说明加载 · 脚本沙箱','知识与记忆\n检索 · 会话上下文'],11.5)

layer(3.3,2.3,'数据持久化')
chips(3.55,4.13,18.4,1.03,['SQLAlchemy 2\n异步 ORM · 仓储','Alembic\n数据库 Schema 迁移','SQLite / MySQL\n定义 · 运行 · 检查点'],12)
label(3.55,3.48,18.4,.50,'内部数据库是事实源；上传的知识文件与形象文件存于本地目录',11.5)

box(23.2,3.3,4.15,14.5,LIGHT_GRAY,GRAY)
label(23.2,16.84,4.15,.7,'工程与交付',14,True,BLUE)
for i,item in enumerate(['Vite · 前端构建','uv · 依赖锁定','Docker / Compose','GitHub Actions','pytest / Ruff','import-linter','业务 / 框架隔离','升级与迁移指引']):
    cell(23.47,15.15-i*1.48,3.61,1.03,item,11.5,LIGHT_BLUE)

layer(.9,1.6,'外部资源',24.05)
chips(3.55,1.20,23.55,1.0,['真实业务 · 模型服务','按需 · 业务接口 / MCP 服务','可选观测 · Langfuse / LangSmith'],12)
label(.65,.19,26.7,.49,'后端使用 Python ≥3.11；框内是技术模块，不是独立微服务。不要求 Redis、消息队列或 Kubernetes。',11.5)
validate()
path=Path(__file__).with_suffix('.png')
plt.savefig(path,dpi=150,bbox_inches='tight',facecolor='white');plt.close()
print(str(path.resolve())+' | text_bounds=PASS | peer_overlap=PASS')
