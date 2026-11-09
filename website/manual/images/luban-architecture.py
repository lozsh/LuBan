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
# Canvas=(18,12), data extent x=0..28,y=0..22. Title y=21.1.
# Layer labels x=.65..2.9; main x=3.3..22.2; governance x=23.2..27.35.
# Entry y=18.6..20.3; config y=14.9..17.8 (gap .8).
# Execution y=11.3..14.1 (gap .8); shared capabilities y=7.5..10.5 (gap .8).
# Storage y=4.5..6.7 (gap .8); foundation y=1.5..3.7 (gap .8).
# Governance y=4.5..17.8. No flow arrows: structure/containment, not request sequence.
# Three peer groups width5.93, inter-group gap=.25; no crossing connectors.
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

label(.65,20.70,26.7,1.02,'LubanAgent 系统架构图',24,True,BLUE)
label(.65,20.26,26.7,.58,'系统组成、模块分工与共用能力',13)
layer(18.6,1.7,'接入入口',24.05)
chips(3.55,18.94,23.55,1.0,['开发者工作台','业务系统 · HTTP API','SSE 流式接口','luban 命令行'],13)

layer(14.9,2.9,'配置管理')
group(3.55,15.14,5.93,2.42,'Agent 管理',['助手定义 · 历史版本','Prompt 提示词库'])
group(9.73,15.14,5.93,2.42,'模型管理',['模型注册 · 用途映射','模型参数 · 连接配置'])
group(15.91,15.14,6.04,2.42,'资源配置',['工具 · MCP · Skill 分配','知识库 · 会话记忆配置'])

layer(11.3,2.8,'任务执行')
group(3.55,12.10,5.93,1.76,'单 Agent',['理解请求 · 调用工具'])
group(9.73,12.10,5.93,1.76,'确定性流程',['业务步骤 · 人工节点'])
group(15.91,12.10,6.04,1.76,'多 Agent 协作',['任务委派 · 结果汇总'])
cell(3.55,11.47,18.40,.52,'统一运行时：上下文组装 · 流式输出 · 检查点与显式续跑',11.3,LIGHT_BLUE)

layer(7.5,3.0,'共用能力')
group(3.55,7.74,5.93,2.52,'模型接入',['OpenAI 兼容模型服务','FakeLLM 本地链路验证'])
group(9.73,7.74,5.93,2.52,'工具与扩展',['Python 业务工具 · MCP','Skill 任务说明与脚本'])
group(15.91,7.74,6.04,2.52,'知识与记忆',['资料管理 · 知识检索','会话历史 · 记忆策略'])

layer(4.5,2.2,'数据存储')
label(3.55,5.89,18.4,.53,'项目数据库 · SQLite / MySQL',13,True,BLUE)
chips(3.55,4.89,18.4,.69,['定义与版本','会话与运行','知识与记忆','调用与成本','检查点与审批'],11.5)

box(23.2,4.5,4.15,13.3,LIGHT_GRAY,GRAY)
label(23.2,16.76,4.15,.75,'运行治理',14,True,BLUE)
for i,item in enumerate(['运行与调用记录','Token 与费用统计','预算与步数限制','超时与取消','工具人工审批','评测与回归']):
    cell(23.47,14.87-i*1.57,3.61,1.14,item,12,LIGHT_BLUE)
label(23.47,4.70,3.61,.88,'共用于三种\n执行方式',11)

layer(1.5,2.2,'工程基座',24.05)
chips(3.55,2.56,23.55,.70,['扩展注册','数据库迁移','Docker 部署','CI 验证','源码升级'],12)
label(3.55,1.76,23.55,.51,'业务代码 app/  →  框架 lubanagent/；框架不反向依赖业务代码',12)
label(.65,.46,26.7,.58,'框内为项目已有模块；业务接口与业务规则由你扩展。层级表示职责，不是请求的固定执行顺序。',11.5)
validate()
path=Path(__file__).with_suffix('.png')
plt.savefig(path,dpi=150,bbox_inches='tight',facecolor='white');plt.close()
print(str(path.resolve())+' | text_bounds=PASS | peer_overlap=PASS')
