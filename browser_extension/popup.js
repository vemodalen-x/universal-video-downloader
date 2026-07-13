const NATIVE_HOST = "com.vemodalen.universal_video_downloader";
const detectButton = document.getElementById("detect");
const statusNode = document.getElementById("status");
const resultsNode = document.getElementById("results");

function setStatus(message, kind = "") {
  statusNode.textContent = message;
  statusNode.className = `status ${kind}`.trim();
}

function scanCurrentFrame() {
  const candidates = new Map();

  function classify(rawUrl, initiatorType = "") {
    if (!rawUrl || rawUrl.startsWith("blob:") || rawUrl.startsWith("data:")) {
      return null;
    }
    let parsed;
    try {
      parsed = new URL(rawUrl, document.baseURI);
    } catch (_error) {
      return null;
    }
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
      return null;
    }
    let searchable = parsed.href.toLowerCase();
    try {
      searchable = decodeURIComponent(searchable);
    } catch (_error) {
      // Keep the original URL when a site uses malformed percent encoding.
    }
    let kind = null;
    if (/\.m3u8(?:$|[?#&])/.test(searchable) || searchable.includes("format=m3u8")) {
      kind = "hls";
    } else if (/\.mpd(?:$|[?#&])/.test(searchable) || searchable.includes("format=mpd")) {
      kind = "dash";
    } else if (/\.(mp4|webm|mov|m4v|mkv|flv)(?:$|[?#&])/.test(searchable)) {
      kind = "video";
    } else if (["video", "audio"].includes(initiatorType) && !/\.(m4s|ts|aac)(?:$|[?#&])/.test(searchable)) {
      kind = "video";
    }
    return kind ? {url: parsed.href, kind} : null;
  }

  function add(rawUrl, initiatorType = "") {
    const candidate = classify(rawUrl, initiatorType);
    if (candidate && !candidates.has(candidate.url)) {
      candidates.set(candidate.url, candidate);
    }
  }

  document.querySelectorAll("video, audio, source").forEach((node) => {
    add(node.currentSrc || node.src || node.getAttribute("src"), node.tagName.toLowerCase());
  });
  document.querySelectorAll('meta[property="og:video"], meta[property="og:video:url"], meta[name="twitter:player:stream"]').forEach((node) => {
    add(node.content);
  });
  document.querySelectorAll("a[href]").forEach((node) => add(node.href));
  performance.getEntriesByType("resource").forEach((entry) => add(entry.name, entry.initiatorType));

  return Array.from(candidates.values()).slice(0, 40);
}

function candidateLabel(candidate) {
  try {
    const parsed = new URL(candidate.url);
    return {
      title: `${candidate.kind.toUpperCase()} · ${parsed.hostname}`,
      detail: parsed.pathname.split("/").filter(Boolean).slice(-2).join("/") || parsed.hostname,
    };
  } catch (_error) {
    return {title: candidate.kind.toUpperCase(), detail: candidate.url};
  }
}

function renderCandidates(candidates, tab) {
  resultsNode.replaceChildren();
  candidates.forEach((candidate) => {
    const row = document.createElement("article");
    row.className = "media-row";
    const copy = document.createElement("div");
    const label = candidateLabel(candidate);
    const title = document.createElement("div");
    title.className = "media-title";
    title.textContent = label.title;
    const detail = document.createElement("div");
    detail.className = "media-meta";
    detail.textContent = label.detail;
    detail.title = candidate.url;
    copy.append(title, detail);

    const send = document.createElement("button");
    send.className = "send";
    send.type = "button";
    send.textContent = "发送";
    send.addEventListener("click", async () => {
      send.disabled = true;
      send.textContent = "发送中";
      try {
        const response = await chrome.runtime.sendNativeMessage(NATIVE_HOST, {
          action: "enqueue",
          candidate: {
            url: candidate.url,
            source_page: tab.url,
            title: tab.title || "浏览器媒体",
            kind: candidate.kind,
          },
        });
        if (!response?.ok) {
          throw new Error(response?.error || "桌面伴侣没有响应");
        }
        send.textContent = "已发送";
        setStatus(response.message, "success");
      } catch (error) {
        send.disabled = false;
        send.textContent = "重试";
        setStatus(`发送失败：${error.message}`, "error");
      }
    });
    row.append(copy, send);
    resultsNode.append(row);
  });
}

detectButton.addEventListener("click", async () => {
  detectButton.disabled = true;
  resultsNode.replaceChildren();
  setStatus("正在检查当前标签页已加载的媒体资源……");
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id || !/^https?:/.test(tab.url || "")) {
      throw new Error("当前页面不是可检测的 HTTP(S) 网页");
    }
    const frameResults = await chrome.scripting.executeScript({
      target: {tabId: tab.id, allFrames: true},
      func: scanCurrentFrame,
    });
    const merged = new Map();
    frameResults.flatMap((result) => result.result || []).forEach((candidate) => merged.set(candidate.url, candidate));
    const candidates = Array.from(merged.values()).sort((left, right) => {
      const rank = {hls: 3, dash: 2, video: 1};
      return rank[right.kind] - rank[left.kind];
    });
    if (!candidates.length) {
      setStatus("没有发现公开媒体地址。请先播放视频几秒后再次检测。", "error");
      return;
    }
    renderCandidates(candidates, tab);
    setStatus(`发现 ${candidates.length} 个候选；请选择一个发送到桌面端。`, "success");
  } catch (error) {
    setStatus(`检测失败：${error.message}`, "error");
  } finally {
    detectButton.disabled = false;
  }
});
