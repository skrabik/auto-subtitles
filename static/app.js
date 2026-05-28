const form = document.querySelector("#generation-form");
const button = document.querySelector("#generate-button");
const messageBox = document.querySelector("#ajax-message");
const progressPanel = document.querySelector("#progress-panel");
const progressFill = document.querySelector("#progress-fill");
const progressPercent = document.querySelector("#progress-percent");
const progressMessage = document.querySelector("#progress-message");
const progressTitle = document.querySelector("#progress-title");
const subtitleEditor = document.querySelector("#subtitle-editor");
const subtitleRows = document.querySelector("#subtitle-rows");
const rerenderButton = document.querySelector("#rerender-button");

let statusTimer = null;
let currentJobId = null;
let currentCaptions = [];

function showMessage(type, html) {
  messageBox.className = `message ${type}`;
  messageBox.innerHTML = html;
}

function hideMessage() {
  messageBox.className = "message hidden";
  messageBox.textContent = "";
}

function setProgress(progress, message) {
  const normalizedProgress = Math.max(0, Math.min(100, Number(progress) || 0));
  progressFill.style.width = `${normalizedProgress}%`;
  progressPercent.textContent = `${normalizedProgress}%`;
  progressMessage.textContent = message || "Working...";
}

function setBusy(isBusy) {
  button.disabled = isBusy;
  button.textContent = isBusy ? "Generating..." : "Generate video";
}

function setEditorBusy(isBusy) {
  rerenderButton.disabled = isBusy;
  rerenderButton.textContent = isBusy ? "Re-rendering..." : "Re-render edited video";
  subtitleRows.querySelectorAll("textarea").forEach((textarea) => {
    textarea.disabled = isBusy;
  });
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(data.error || `Request failed with status ${response.status}`);
  }

  return data;
}

function formatTime(seconds) {
  const safeSeconds = Math.max(0, Number(seconds) || 0);
  const minutes = Math.floor(safeSeconds / 60);
  const remainingSeconds = (safeSeconds % 60).toFixed(2).padStart(5, "0");
  return `${minutes}:${remainingSeconds}`;
}

function collectEditedCaptions() {
  return currentCaptions.map((caption, index) => {
    const textarea = subtitleRows.querySelector(`[data-caption-index="${index}"]`);
    return {
      start: caption.start,
      end: caption.end,
      text: textarea ? textarea.value : caption.text,
    };
  });
}

function renderSubtitleEditor(captions) {
  currentCaptions = Array.isArray(captions) ? captions : [];
  subtitleRows.innerHTML = "";

  currentCaptions.forEach((caption, index) => {
    const row = document.createElement("label");
    row.className = "caption-row";

    const time = document.createElement("span");
    time.className = "caption-time";
    time.textContent = `${formatTime(caption.start)} - ${formatTime(caption.end)}`;

    const textarea = document.createElement("textarea");
    textarea.dataset.captionIndex = String(index);
    textarea.rows = 2;
    textarea.value = caption.text || "";

    row.append(time, textarea);
    subtitleRows.append(row);
  });

  subtitleEditor.classList.toggle("hidden", currentCaptions.length === 0);
  setEditorBusy(false);
}

function stopPolling() {
  if (statusTimer !== null) {
    clearInterval(statusTimer);
    statusTimer = null;
  }
}

async function pollStatus(jobId) {
  try {
    const job = await fetchJson(`/status/${jobId}`);
    setProgress(job.progress, job.message);

    if (job.status === "completed") {
      stopPolling();
      setBusy(false);
      setEditorBusy(false);
      progressTitle.textContent = "Done";
      showMessage(
        "success",
        `Video is ready: <a href="${job.result.download_url}">download ${job.result.filename}</a>`,
      );
      renderSubtitleEditor(job.captions);
      return;
    }

    if (job.status === "error") {
      stopPolling();
      setBusy(false);
      setEditorBusy(false);
      progressTitle.textContent = "Error";
      showMessage("error", job.error || "Generation failed.");
    }
  } catch (error) {
    stopPolling();
    setBusy(false);
    setEditorBusy(false);
    progressTitle.textContent = "Error";
    showMessage("error", error.message);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  stopPolling();
  hideMessage();
  subtitleEditor.classList.add("hidden");
  subtitleRows.innerHTML = "";
  currentCaptions = [];
  currentJobId = null;
  setBusy(true);
  progressPanel.classList.remove("hidden");
  progressTitle.textContent = "Generating video...";
  setProgress(0, "Uploading video...");

  try {
    const data = await fetchJson(form.action, {
      method: "POST",
      body: new FormData(form),
    });

    currentJobId = data.job_id;
    setProgress(0, "Queued...");
    statusTimer = setInterval(() => pollStatus(currentJobId), 1500);
    await pollStatus(currentJobId);
  } catch (error) {
    setBusy(false);
    progressTitle.textContent = "Error";
    setProgress(100, "Failed.");
    showMessage("error", error.message);
  }
});

rerenderButton.addEventListener("click", async () => {
  if (!currentJobId) {
    showMessage("error", "Generate a video first.");
    return;
  }

  stopPolling();
  hideMessage();
  setEditorBusy(true);
  progressPanel.classList.remove("hidden");
  progressTitle.textContent = "Rendering edited video...";
  setProgress(60, "Uploading edited subtitles...");

  try {
    await fetchJson(`/rerender/${currentJobId}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ captions: collectEditedCaptions() }),
    });

    statusTimer = setInterval(() => pollStatus(currentJobId), 1500);
    await pollStatus(currentJobId);
  } catch (error) {
    setEditorBusy(false);
    progressTitle.textContent = "Error";
    setProgress(100, "Failed.");
    showMessage("error", error.message);
  }
});
