/**
 * @google/model-viewer web component 的 JSX 类型声明。
 * 库本体经 agent-avatar.tsx 动态 import 注册自定义元素，这里只补 React JSX 标签类型
 * （react-jsx 转换下 IntrinsicElements 在 react 模块的 JSX 命名空间内；
 * 先 import 'react' 使 declare module 成为增强而非覆盖）。
 */

import 'react';

interface ModelViewerAttributes {
  src: string;
  'camera-controls': boolean;
  'touch-action'?: string;
  'interaction-prompt'?: string;
  style: React.CSSProperties;
  ref?: React.Ref<HTMLElement>;
  [key: string]: unknown;
}

declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      'model-viewer': ModelViewerAttributes;
    }
  }
}

declare module '@google/model-viewer';
