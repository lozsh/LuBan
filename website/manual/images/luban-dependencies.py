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
from pathlib import Path
import matplotlib
matplotlib.use('Agg')

# All positions below are data coordinates; text is validated against its panel.
def panel(ax, x, y, w, h, title, body, edge, fill, fontsize=13):
    ax.add_patch(mpatches.FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.08,rounding_size=0.18',linewidth=1.4,edgecolor=edge,facecolor=fill))
    ax.text(x+0.42,y+h-0.38,safe_text(title),ha='left',va='top',fontsize=fontsize+2,fontweight='bold',color=edge)
    text=ax.text(x+0.42,y+h-1.04,safe_text(body),ha='left',va='top',fontsize=fontsize,color=GRAY,linespacing=1.65)
    return text, (x,y,w,h)

def validate(fig, ax, panels):
    fig.canvas.draw()
    for text,(x,y,w,h) in panels:
        bounds=text.get_window_extent().transformed(ax.transData.inverted())
        assert bounds.x0 >= x and bounds.x1 <= x+w, (text.get_text(),bounds)
        assert bounds.y0 >= y and bounds.y1 <= y+h, (text.get_text(),bounds)

def arrow(ax, x1,y1,x2,y2, dashed=False):
    ax.annotate('',xy=(x2,y2),xytext=(x1,y1),arrowprops=dict(arrowstyle='->',color=GRAY,lw=1.7,linestyle='--' if dashed else '-'))

# Layout design (calculated before drawing):
# figure=(18,12), xlim=(0,24), ylim=(0,18).
# app x9..15,y8.0..10.6; libs x7..17,y13.4..15.8 -> vertical gap2.8.
# model x.6..5.6,y8.0..10.6; obs x18.4..23.4 same y -> horizontal gap3.4.
# database x9..15,y3.8..6.4 -> vertical gap1.6; business and MCP same bottom row.
# optional elbows in x7.2 /16.8 gutters, never across panel interiors.
fig,ax=plt.subplots(figsize=(18,12));ax.set_xlim(0,24);ax.set_ylim(0,18);ax.axis('off')
ax.text(.6,17.25,safe_text('系统依赖：哪些在程序里，哪些要单独连接'),fontsize=24,fontweight='bold',color=BLUE)
ax.text(.6,16.55,safe_text('箭头：使用方 → 被依赖方     |     实线：运行关系     |     虚线：按场景启用'),fontsize=13,color=GRAY)
checks=[]
checks.append(panel(ax,7,13.4,10,2.4,'Python 程序里的库，不是独立服务','FastAPI：接口     ·     LangChain：模型 / 工具接入\nLangGraph：任务执行     ·     SQLAlchemy：数据库访问',BLUE,LIGHT_BLUE,13))
checks.append(panel(ax,9,8,6,2.6,'LubanAgent 应用','Python 后端 + 构建后的工作台\n你部署和定制的主体',BLUE,LIGHT_BLUE,14))
checks.append(panel(ax,.6,8,5,2.6,'真实业务：模型服务','DeepSeek / Qwen / GLM 等\n或 Ollama / vLLM 本地服务',PURPLE,LIGHT_PURPLE,12))
checks.append(panel(ax,18.4,8,5,2.6,'可选：观测平台','Langfuse 自部署 / LangSmith\n关闭后仍保留内部运行记录',ORANGE,LIGHT_ORANGE,12))
checks.append(panel(ax,9,3.8,6,2.6,'项目数据库：必须','SQLite 文件 / MySQL 服务\n配置、版本、知识、运行记录、检查点',GREEN,LIGHT_GREEN,11.5))
checks.append(panel(ax,.6,3.8,5,2.6,'按业务：你自己的系统','订单 / 工单 / 内部服务\nPython 业务工具去调用',GREEN,LIGHT_GREEN,12))
checks.append(panel(ax,18.4,3.8,5,2.6,'可选：外部工具与数据','MCP Server / 向量模型等\n根据业务和检索要求接入',GREEN,LIGHT_GREEN,12))
arrow(ax,12,10.6,12,13.4)
arrow(ax,9,9.3,5.6,9.3,True)
arrow(ax,15,9.3,18.4,9.3,True)
arrow(ax,12,8,12,6.4)
# Optional business integrations, elbow lines avoid crossing unrelated services.
for start,gutter,end in [(9,7.2,5.6),(15,16.8,18.4)]:
    ax.plot([start,gutter,gutter],[8.35,8.35,5.1],color=GRAY,lw=1.5,linestyle='--')
    arrow(ax,gutter,5.1,end,5.1,True)
ax.text(.6,2.55,safe_text('最小起步'),fontsize=16,fontweight='bold',color=BLUE)
ax.text(3.3,2.55,safe_text('Python + SQLite + FakeLLM 固定回复；不接真实模型也能验证链路'),fontsize=14,color=GRAY)
ax.text(.6,1.65,safe_text('真实业务'),fontsize=16,fontweight='bold',color=GREEN)
ax.text(3.3,1.65,safe_text('再连接真实模型、业务接口；观测与外部工具按需接入'),fontsize=14,color=GRAY)
ax.text(.6,.65,safe_text('Node.js 只用于前端开发 / 构建。文档站独立发布；Docker 五服务组合是演示，不是最低运行要求。'),fontsize=12,color=GRAY)
validate(fig,ax,checks)
path=Path(__file__).with_suffix('.png')
plt.savefig(path,dpi=150,bbox_inches='tight',facecolor='white');plt.close()
print(str(path.resolve()))
