export interface LoreEmbedConfig {
  baseUrl: string;
  authToken?: string;
  mode: "page" | "search" | "capture";
  pageId?: string;
  query?: string;
  container?: string;
  theme?: "light" | "dark" | "auto";
}

export class LoreEmbed {
  readonly config: LoreEmbedConfig;
  readonly iframe: HTMLIFrameElement;
  private readonly resizeHandler: (event: MessageEvent) => void;

  constructor(config: LoreEmbedConfig) {
    this.config = normalizeConfig(config);
    const container = document.querySelector<HTMLElement>(this.config.container ?? "#lore-embed");
    if (!container) {
      throw new Error(`Lore embed container not found: ${this.config.container ?? "#lore-embed"}`);
    }

    this.iframe = document.createElement("iframe");
    this.iframe.src = embedUrl(this.config);
    this.iframe.title = "Lore embed";
    this.iframe.loading = "lazy";
    this.iframe.style.width = "100%";
    this.iframe.style.border = "0";
    this.iframe.style.display = "block";
    this.iframe.style.overflow = "hidden";
    this.iframe.setAttribute("scrolling", "no");

    this.resizeHandler = (event: MessageEvent) => {
      if (event.source !== this.iframe.contentWindow || !event.data || event.data.type !== "lore:resize") {
        return;
      }
      const height = Number(event.data.height);
      if (Number.isFinite(height) && height > 0) {
        this.iframe.style.height = `${Math.ceil(height)}px`;
      }
    };

    window.addEventListener("message", this.resizeHandler);
    this.iframe.addEventListener("load", () => this.postAuthToken());
    container.replaceChildren(this.iframe);
  }

  postAuthToken(): void {
    if (!this.config.authToken || !this.iframe.contentWindow) {
      return;
    }
    this.iframe.contentWindow.postMessage(
      { type: "lore:auth", authToken: this.config.authToken },
      new URL(this.config.baseUrl).origin,
    );
  }

  destroy(): void {
    window.removeEventListener("message", this.resizeHandler);
    this.iframe.remove();
  }
}

export function mountLoreEmbed(config: LoreEmbedConfig): LoreEmbed {
  return new LoreEmbed(config);
}

function normalizeConfig(config: LoreEmbedConfig): LoreEmbedConfig {
  if (!config.baseUrl) {
    throw new Error("Lore embed requires baseUrl.");
  }
  if (!config.mode) {
    throw new Error("Lore embed requires mode.");
  }
  if (config.mode === "page" && !config.pageId) {
    throw new Error("Lore page embeds require pageId.");
  }
  return {
    ...config,
    baseUrl: config.baseUrl.replace(/\/+$/, ""),
    container: config.container ?? "#lore-embed",
    theme: config.theme ?? "auto",
  };
}

function embedUrl(config: LoreEmbedConfig): string {
  const url = new URL(`${config.baseUrl}/embed`);
  url.searchParams.set("mode", config.mode);
  url.searchParams.set("theme", config.theme ?? "auto");
  url.searchParams.set("hideChrome", "true");
  if (config.pageId) {
    url.searchParams.set("pageId", config.pageId);
  }
  if (config.mode === "search" && config.query) {
    url.searchParams.set("q", config.query);
  }
  return url.toString();
}

declare global {
  interface Window {
    LoreEmbedConfig?: LoreEmbedConfig;
    LoreEmbed?: {
      mount: typeof mountLoreEmbed;
      LoreEmbed: typeof LoreEmbed;
    };
  }
}

if (typeof window !== "undefined") {
  window.LoreEmbed = { mount: mountLoreEmbed, LoreEmbed };
  if (window.LoreEmbedConfig) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => mountLoreEmbed(window.LoreEmbedConfig as LoreEmbedConfig), {
        once: true,
      });
    } else {
      mountLoreEmbed(window.LoreEmbedConfig);
    }
  }
}
