(function (global) {
  "use strict";

  function normalizeConfig(config) {
    if (!config || !config.baseUrl) {
      throw new Error("Lore embed requires baseUrl.");
    }
    if (!config.mode) {
      throw new Error("Lore embed requires mode.");
    }
    if (config.mode === "page" && !config.pageId) {
      throw new Error("Lore page embeds require pageId.");
    }
    return {
      baseUrl: String(config.baseUrl).replace(/\/+$/, ""),
      authToken: config.authToken,
      mode: config.mode,
      pageId: config.pageId,
      container: config.container || "#lore-embed",
      theme: config.theme || "auto"
    };
  }

  function embedUrl(config) {
    var url = new URL(config.baseUrl + "/embed");
    url.searchParams.set("mode", config.mode);
    url.searchParams.set("theme", config.theme);
    url.searchParams.set("hideChrome", "true");
    if (config.pageId) {
      url.searchParams.set("pageId", config.pageId);
    }
    return url.toString();
  }

  function LoreEmbed(config) {
    this.config = normalizeConfig(config);
    var container = document.querySelector(this.config.container);
    if (!container) {
      throw new Error("Lore embed container not found: " + this.config.container);
    }

    var iframe = document.createElement("iframe");
    this.iframe = iframe;
    iframe.src = embedUrl(this.config);
    iframe.title = "Lore embed";
    iframe.loading = "lazy";
    iframe.style.width = "100%";
    iframe.style.border = "0";
    iframe.style.display = "block";
    iframe.style.overflow = "hidden";
    iframe.setAttribute("scrolling", "no");

    var self = this;
    this.resizeHandler = function (event) {
      if (event.source !== iframe.contentWindow || !event.data || event.data.type !== "lore:resize") {
        return;
      }
      var height = Number(event.data.height);
      if (Number.isFinite(height) && height > 0) {
        iframe.style.height = Math.ceil(height) + "px";
      }
    };

    window.addEventListener("message", this.resizeHandler);
    iframe.addEventListener("load", function () {
      self.postAuthToken();
    });
    container.replaceChildren(iframe);
  }

  LoreEmbed.prototype.postAuthToken = function () {
    if (!this.config.authToken || !this.iframe.contentWindow) {
      return;
    }
    this.iframe.contentWindow.postMessage(
      { type: "lore:auth", authToken: this.config.authToken },
      new URL(this.config.baseUrl).origin
    );
  };

  LoreEmbed.prototype.destroy = function () {
    window.removeEventListener("message", this.resizeHandler);
    this.iframe.remove();
  };

  global.LoreEmbed = {
    LoreEmbed: LoreEmbed,
    mount: function (config) {
      return new LoreEmbed(config);
    }
  };
})(typeof window !== "undefined" ? window : globalThis);
