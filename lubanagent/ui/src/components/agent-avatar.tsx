/**
 * Agent 虚拟形象展示（三分派）：
 * - svg / png → <img>（H13：带内存态身份头 authedFetch 拉取为 blob URL，不用裸 src——
 *   /v1/* 全部需鉴权，裸 <img src> 无法附带 Authorization，鉴权开启时会静默 401 失效）
 * - glb / gltf → <model-viewer>（@google/model-viewer web component，动态 import——
 *   bundle 不膨胀、jsdom 测试不触真 3D 库；camera-controls 用户拖拽查看）
 *   GLB 内嵌 talk 动画（嘴巴开合）：speaking=true 时播放、结束停回闭嘴帧
 *   （状态驱动的确定性动效，不用 auto-rotate 空转）
 * - 无 avatar / 拉取失败（含 401）→ 回退 MessageCircle 图标（401 同时登记鉴权提示）
 */

import { MessageCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { authedFetch, API_BASE } from '@/api/client';
import { cn } from '@/lib/utils';

interface AvatarAgent {
  key: string;
  avatar?: string | null;
}

const AVATAR_URL = (key: string) => `${API_BASE}/v1/agents/${encodeURIComponent(key)}/avatar`;

function avatarKind(filename: string): 'image' | 'model3d' | null {
  const lower = filename.toLowerCase();
  if (lower.endsWith('.svg') || lower.endsWith('.png')) return 'image';
  if (lower.endsWith('.glb') || lower.endsWith('.gltf')) return 'model3d';
  return null;
}

/** 带身份的 blob 加载：/v1 资源不能裸请求（H13）；401 等失败回退图标而非静默破图；null 跳过 */
function useAuthedBlobUrl(url: string | null): { objectUrl: string | null; failed: boolean } {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (url === null) return;
    let cancelled = false;
    let created: string | null = null;
    setObjectUrl(null);
    setFailed(false);
    void (async () => {
      try {
        const response = await authedFetch(url);
        if (!response.ok) {
          if (!cancelled) setFailed(true); // 401 已由 authedFetch 统一登记
          return;
        }
        const blob = await response.blob();
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      if (created !== null) URL.revokeObjectURL(created);
    };
  }, [url]);

  return { objectUrl, failed };
}

/** model-viewer 动画控制面（web component 属性的最小子集，v4 无元素级 stop()） */
interface ModelViewerApi extends HTMLElement {
  play: () => void;
  pause: () => void;
  currentTime: number;
  availableAnimations?: string[];
}

/** 3D 形象容器：blob URL 加载（带身份）+ 动态加载 model-viewer；speaking 驱动说话动画 */
function Model3DAvatar({ url, size, speaking }: { url: string; size: number; speaking?: boolean }) {
  const ref = useRef<HTMLElement | null>(null);
  const [ready, setReady] = useState(false);
  const { objectUrl, failed } = useAuthedBlobUrl(url);

  useEffect(() => {
    let cancelled = false;
    void import('@google/model-viewer').then(() => {
      if (!cancelled) setReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // 说话 = 播放内嵌 talk 动画；结束 pause + 归零停回第 0 帧（闭嘴）——v4 元素级 API
  // 没有 stop()。模型未加载完时 play 会失败，静默忽略——下一次 speaking 变化重试即可
  useEffect(() => {
    if (!ready) return;
    const mv = ref.current as unknown as ModelViewerApi | null;
    if (!mv) return;
    try {
      if (speaking) mv.play();
      else {
        mv.pause();
        mv.currentTime = 0;
      }
    } catch {
      /* 动画尚未就绪 */
    }
  }, [speaking, ready]);

  // React 18 对未注册自定义元素按未知元素渲染：库加载前先占位，加载后正确升级
  if (failed || objectUrl === null) {
    return (
      <MessageCircle
        className="shrink-0 text-primary"
        size={size}
        data-testid="agent-avatar-fallback"
      />
    );
  }
  return (
    <model-viewer
      ref={ref}
      src={objectUrl}
      camera-controls
      touch-action="pan-y"
      interaction-prompt="none"
      style={{ width: size, height: size, borderRadius: '50%' }}
      data-testid="agent-avatar-3d"
    />
  );
}

export interface AgentAvatarProps {
  agent: AvatarAgent;
  /** 展示尺寸（正方形/圆形，px） */
  size?: number;
  className?: string;
  /** 3D 形象说话中（播放内嵌 talk 动画；svg/png 忽略） */
  speaking?: boolean;
}

export function AgentAvatar({ agent, size = 32, className, speaking }: AgentAvatarProps) {
  const kind = agent.avatar ? avatarKind(agent.avatar) : null;
  // 仅确实配置了形象才发起鉴权拉取（无形象不产生无谓 404）
  const url = kind !== null ? AVATAR_URL(agent.key) : null;
  const { objectUrl, failed } = useAuthedBlobUrl(url);
  const visible = objectUrl !== null && !failed; // 身份/网络失败 → 回退图标而非静默破图
  if (kind === 'image') {
    if (!visible) {
      return (
        <MessageCircle
          className={cn('shrink-0 text-primary', className)}
          size={size}
          data-testid="agent-avatar-fallback"
        />
      );
    }
    return (
      <img
        src={objectUrl as string}
        alt={agent.key}
        className={cn('shrink-0 rounded-full object-cover', className)}
        style={{ width: size, height: size }}
        data-testid="agent-avatar-img"
      />
    );
  }
  if (kind === 'model3d') {
    return <Model3DAvatar url={AVATAR_URL(agent.key)} size={size} speaking={speaking} />;
  }
  // 无形象（或未知后缀）：回退聊天图标
  return (
    <MessageCircle
      className={cn('shrink-0 text-primary', className)}
      size={size}
      data-testid="agent-avatar-fallback"
    />
  );
}
