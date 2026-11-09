// 自定义主题：沿用 VitePress 默认主题，叠加品牌样式与代码块增强
// 这是 VitePress 官方推荐的定制方式，高星项目（Vite/Vitest/Element Plus）均采用此结构
import type { Theme } from "vitepress";
import DefaultTheme from "vitepress/theme";
import {
  computed,
  defineComponent,
  h,
  nextTick,
  onMounted,
  onUnmounted,
  ref,
  watch,
} from "vue";
import { useData, useRoute } from "vitepress";
import "./custom.css";

/** 折叠后保留的可见行数（与 custom.css 中 nth-child(n + X) 保持一致：X = VISIBLE + 1） */
const VISIBLE_LINES = 16;
/** 总行数超过该值才折叠（短代码块折叠反而添乱） */
const MIN_LINES_TO_COLLAPSE = VISIBLE_LINES + 6;

const WIDE_SCREEN_KEY = "luban-docs-wide";

/** 阅读宽度开关：只影响文档页，存储不可用时仍可在当前页面切换。 */
const WideScreenToggle = defineComponent({
  name: "WideScreenToggle",
  setup() {
    const wide = ref(false);
    const { frontmatter, page } = useData();
    const isDocument = computed(
      () =>
        !page.value.isNotFound &&
        (!frontmatter.value.layout || frontmatter.value.layout === "doc"),
    );
    const applyWidth = () => {
      if (typeof document !== "undefined") {
        document.documentElement.classList.toggle(
          "docs-wide",
          wide.value && isDocument.value,
        );
      }
    };

    onMounted(() => {
      try {
        wide.value = localStorage.getItem(WIDE_SCREEN_KEY) === "true";
      } catch {
        // 隐私模式或禁用存储时，使用当前页面状态。
      }
      applyWidth();
    });
    watch([wide, isDocument], applyWidth, { flush: "post" });
    onUnmounted(() => {
      document.documentElement.classList.remove("docs-wide");
    });

    return () =>
      isDocument.value
        ? h(
            "button",
            {
              type: "button",
              class: "wide-screen-toggle",
              "aria-label": wide.value ? "切换到标准宽度" : "开启宽屏模式",
              "aria-pressed": wide.value,
              title: wide.value
                ? "切换到标准宽度"
                : "开启宽屏模式，展开表格阅读区域",
              onClick: () => {
                wide.value = !wide.value;
                try {
                  localStorage.setItem(WIDE_SCREEN_KEY, String(wide.value));
                } catch {
                  // 不依赖存储成功才能改变阅读宽度。
                }
              },
            },
            [
              h(
                "svg",
                {
                  viewBox: "0 0 24 24",
                  width: 18,
                  height: 18,
                  fill: "none",
                  stroke: "currentColor",
                  "stroke-width": 1.7,
                  "stroke-linecap": "round",
                  "stroke-linejoin": "round",
                  "aria-hidden": "true",
                },
                [
                  h("rect", { x: 3, y: 5, width: 18, height: 14, rx: 2 }),
                  h("path", {
                    d: wide.value
                      ? "m8 10 2 2-2 2m8-4-2 2 2 2"
                      : "m9 10-2 2 2 2m6-4 2 2-2 2",
                  }),
                ],
              ),
              h(
                "span",
                { class: "wide-screen-label" },
                wide.value ? "标准宽度" : "宽屏模式",
              ),
            ],
          )
        : null;
  },
});

/**
 * 代码块折叠增强（社区成熟模式：客户端增强 + CSS 按行隐藏）
 * - 超过阈值的代码块自动折叠，底部出现"展开全部 N 行 / 收起"按钮
 * - 已增强的代码块打 data 标记，避免重复处理（HMR / 前进后退均安全）
 */
function enhanceCodeBlocks(): void {
  // SSR（构建期渲染 HTML）没有 document，增强只在浏览器端执行
  if (typeof document === "undefined") return;
  document
    .querySelectorAll<HTMLDivElement>('.vp-doc div[class*="language-"]')
    .forEach((el) => {
      if (el.dataset.collapsible === "done") return;
      el.dataset.collapsible = "done";

      const total = el.querySelectorAll("pre code .line").length;
      if (total <= MIN_LINES_TO_COLLAPSE) return;

      el.classList.add("can-collapse", "is-collapsed");

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "code-collapse-toggle";
      btn.textContent = `展开全部 ${total} 行`;
      btn.addEventListener("click", () => {
        const collapsed = el.classList.toggle("is-collapsed");
        btn.textContent = collapsed ? `展开全部 ${total} 行` : "收起代码";
        btn.setAttribute("aria-expanded", String(!collapsed));
      });
      el.appendChild(btn);
    });
}

export default {
  extends: DefaultTheme,
  Layout: () =>
    h(DefaultTheme.Layout, null, {
      "nav-bar-content-after": () => h(WideScreenToggle),
    }),
  setup() {
    const route = useRoute();
    watch(
      () => route.path,
      async () => {
        await nextTick();
        enhanceCodeBlocks();
      },
      { immediate: true, flush: "post" },
    );
  },
} satisfies Theme;
