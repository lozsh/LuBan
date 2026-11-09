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
# main panels x=.8..18.4; governance x=20.2..23.2 -> horizontal gap1.8.
# entry y=14.1..16.1; config y=10.6..13.1 -> vertical gap1.0.
# execution y=7.0..9.6 -> gap1.0; capabilities y=3.2..6.0 -> gap1.0.
# arrows are vertical at x=9.6 and exactly meet panel borders.
fig,ax=plt.subplots(figsize=(18,12))
ax.set_xlim(0,24);ax.set_ylim(0,18);ax.axis('off')
ax.text(.8,17.35,safe_text('LubanAgent：拿到源码后，你已经有哪些功能'),fontsize=24,fontweight='bold',color=BLUE)
ax.text(.8,16.72,safe_text('内部功能架构  /  框内是项目已提供的模块，不是一次请求的固定流程'),fontsize=13,color=GRAY)
checks=[]
checks.append(panel(ax,.8,14.1,17.6,2.0,'接入与开发入口','开发者工作台：配置、调试、查看记录     |     业务系统：HTTP 接口、流式响应     |     命令行',BLUE,LIGHT_BLUE,13))
checks.append(panel(ax,.8,10.6,17.6,2.5,'助手配置中心：决定它知道什么、能用什么','Agent 定义与历史版本    ·    Prompt 提示词库    ·    模型注册与用途映射\n工具 / Skill 分配    ·    知识与记忆配置',BLUE,LIGHT_BLUE,14))
checks.append(panel(ax,.8,7.0,17.6,2.6,'任务执行：三种方式共用一套工程','单 Agent：理解请求、调用工具、生成答复\n代码流程：固定步骤与人工节点     |     多 Agent：调度助手委派给专业助手',PURPLE,LIGHT_PURPLE,14))
checks.append(panel(ax,.8,3.2,17.6,2.8,'共用能力：连接模型、数据与业务','模型工厂    ·    Python 业务工具    ·    MCP 外部工具连接\nSkill 任务说明与脚本    ·    知识检索    ·    会话记忆',GREEN,LIGHT_GREEN,14))
for top,bottom in [(14.1,13.1),(10.6,9.6),(7.0,6.0)]:arrow(ax,9.6,top,9.6,bottom)
checks.append(panel(ax,20.2,3.2,3.0,9.9,'运行与治理','预算与步数\n超时与取消\n工具人工审批\n运行 / 调用记录\nToken 与费用\n评测与回归\n检查点与显式续跑',ORANGE,LIGHT_ORANGE,12))
# Governance is cross-cutting, not an extra serial execution step.
ax.plot([19.2,19.2],[3.2,13.1],color=ORANGE,lw=1.5,linestyle='--')
ax.text(.8,1.92,safe_text('你来开发 app/'),fontsize=16,fontweight='bold',color=GREEN)
ax.text(4.5,1.92,safe_text('业务接口、权限与金额规则、流程 / 团队代码、初始定义与验收数据'),fontsize=14,color=GRAY)
ax.text(.8,.98,safe_text('框架提供 lubanagent/'),fontsize=16,fontweight='bold',color=BLUE)
ax.text(6.6,.98,safe_text('上面的执行与管理能力，以及数据库迁移、Docker、CI、升级指引'),fontsize=14,color=GRAY)
validate(fig,ax,checks)
path=Path(__file__).with_suffix('.png')
plt.savefig(path,dpi=150,bbox_inches='tight',facecolor='white');plt.close()
print(str(path.resolve()))
