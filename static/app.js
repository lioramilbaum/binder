const foldersElement = document.querySelector("#folders");
const emptyState = document.querySelector("#empty-state");
const folderCount = document.querySelector("#folder-count");
const sidebarFolders = document.getElementById("sidebar-folders");
const sidebarToggle = document.getElementById("sidebar-toggle");
const sidebarScrim = document.getElementById("sidebar-scrim");
const topbarNewFolderButton = document.getElementById("topbar-new-folder-button");
const dialog = document.querySelector("#folder-dialog");
const form = document.querySelector("#folder-form");
const formError = document.querySelector("#form-error");
const homeView = document.querySelector("#home-view");
const folderView = document.querySelector("#folder-view");
const documentsElement = document.querySelector("#documents");
const documentsEmptyState = document.querySelector("#documents-empty-state");
const exportDocumentsButton = document.querySelector("#export-documents-button");
const documentSearch = document.querySelector("#document-search");
const documentDialog = document.querySelector("#document-dialog");
const documentForm = document.querySelector("#document-form");
const documentFormError = document.querySelector("#document-form-error");
const importAiThreadButton = document.querySelector("#import-ai-thread-button");
const aiShareDialog = document.querySelector("#ai-share-dialog");
const documentTypeSelect = document.querySelector("#document-type");
const aiShareForm = document.querySelector("#ai-share-form");
const aiShareConfirmForm = document.querySelector("#ai-share-confirm-form");
const aiShareError = document.querySelector("#ai-share-error");
const aiShareConfirmError = document.querySelector("#ai-share-confirm-error");
let aiSharePreviewId = null;
const shareErrors = {
  invalid_link: @@share_invalid_link@@,
  inaccessible_link: @@share_inaccessible_link@@,
  too_large: @@share_too_large@@,
  unsupported_format: @@share_unsupported_format@@,
  preview_unavailable: @@share_preview_unavailable@@,
  invalid_date: @@share_invalid_date@@,
  invalid_time: @@share_invalid_time@@,
  folder_not_found: @@share_folder_not_found@@,
  invalid_request: @@share_invalid_request@@,
};
const linkDialog = document.querySelector("#link-dialog");
const linkForm = document.querySelector("#link-form");
const linkTypeSelect = document.querySelector("#link-type-select");
const linkTypesEmptyHint = document.querySelector("#link-types-empty-hint");
const newLinkTypeInput = document.querySelector("#new-link-type-input");
const addLinkTypeButton = document.querySelector("#add-link-type-button");
const linkTargetSelect = document.querySelector("#link-target-select");
const linkFormError = document.querySelector("#link-form-error");
const closeLinkDialogButton = document.querySelector("#close-link-dialog");
const cancelLinkDialogButton = document.querySelector("#cancel-link-dialog");
const linkErrors = {
  duplicate_link: @@link_error_duplicate@@,
  self_link: @@link_error_self@@,
  target_not_found: @@link_error_target_not_found@@,
  link_type_not_found: @@link_error_type_not_found@@,
  invalid_link_type: @@link_error_invalid_type@@,
  document_not_found: @@link_error_document_not_found@@,
  invalid_request: @@link_error_invalid_type@@,
};
let currentFolderId = null;
let linkSourceDocumentId = null;
let currentDocumentId = null;
let currentDocuments = [];
let documentTags = [];
const selectedFilterTags = new Set();

const dateFormatter = new Intl.DateTimeFormat(@@date_locale@@, {
  day: "numeric",
  month: "long",
  year: "numeric",
});

function localDateTimeInputs(utcValue) {
  if (!utcValue) return null;
  const value = new Date(utcValue);
  const year = value.getFullYear();
  if (Number.isNaN(value.getTime()) || year < 1 || year > 9999) return null;
  const twoDigits = (number) => String(number).padStart(2, "0");
  return {
    date: `${String(year).padStart(4, "0")}-${twoDigits(value.getMonth() + 1)}-${twoDigits(value.getDate())}`,
    time: `${twoDigits(value.getHours())}:${twoDigits(value.getMinutes())}`,
  };
}

function format(template, values) {
  return template.replace(/\{(\w+)\}/g, (_, key) => values[key]);
}

function countLabel(count, one, other) {
  return count === 1 ? one : format(other, { count });
}

function setupFileDropZone(inputId, zoneId, listId, pasteTargetId) {
  const input = document.querySelector(`#${inputId}`);
  const zone = document.querySelector(`#${zoneId}`);
  const list = document.querySelector(`#${listId}`);
  const pasteTarget = document.querySelector(`#${pasteTargetId}`);

  function renderSelectedFiles() {
    list.replaceChildren();
    for (const file of input.files) {
      const item = document.createElement("span");
      item.textContent = `${file.name} (${formatFileSize(file.size)})`;
      list.append(item);
    }
  }

  function addFiles(files) {
    const transfer = new DataTransfer();
    for (const file of input.files) transfer.items.add(file);
    for (const file of files) transfer.items.add(file);
    input.files = transfer.files;
    renderSelectedFiles();
  }

  zone.addEventListener("click", (event) => {
    if (event.target !== input && !event.target.closest("button")) input.click();
  });
  zone.addEventListener("keydown", (event) => {
    if (event.target.closest("button")) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.click();
    }
  });
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("dragging");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dragging");
    addFiles(event.dataTransfer.files);
  });
  input.addEventListener("change", renderSelectedFiles);
  pasteTarget.addEventListener("paste", (event) => {
    const files = [...event.clipboardData.files];
    if (files.length) {
      event.preventDefault();
      addFiles(files);
    }
  });

  return {
    addFiles,
    clear: () => {
      input.value = "";
      renderSelectedFiles();
    },
  };
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const tagKey = (name) => name.trim().replace(/\s+/g, " ").toLowerCase();

function renderTagEditor() {
  const tagList = document.querySelector("#document-tags");
  tagList.replaceChildren();
  for (const tag of documentTags) {
    const chip = document.createElement("span");
    chip.className = "tag-chip";
    chip.textContent = tag;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "×";
    button.className = "tag-remove";
    button.setAttribute("aria-label", format(@@remove_tag@@, { name: tag }));
    button.addEventListener("click", (e) => {
      e.preventDefault();
      documentTags = documentTags.filter((t) => t !== tag);
      renderTagEditor();
    });
    chip.append(button);
    tagList.append(chip);
  }
}

function commitTagInput() {
  const input = document.querySelector("#document-tag-input");
  const value = input.value.trim();
  if (!value) return;
  const newTags = value.split(",").map((t) => t.trim()).filter((t) => t);
  const uniqueKeys = new Set(documentTags.map(tagKey));
  for (const tag of newTags) {
    if (!uniqueKeys.has(tagKey(tag))) {
      documentTags.push(tag);
      uniqueKeys.add(tagKey(tag));
    }
  }
  input.value = "";
  renderTagEditor();
}

async function loadTagSuggestions() {
  try {
    const response = await fetch("/api/tags");
    const tags = await response.json();
    const datalist = document.querySelector("#tag-suggestions");
    datalist.replaceChildren();
    for (const tag of tags) {
      const option = document.createElement("option");
      option.value = tag.name;
      datalist.append(option);
    }
  } catch {
    // Silently ignore failures
  }
}

function renderTagFilter() {
  const container = document.querySelector("#tag-filter");
  const distinctTags = new Set();
  for (const doc of currentDocuments) {
    for (const tag of doc.tags || []) {
      distinctTags.add(tag);
    }
  }
  const tags = Array.from(distinctTags).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
  container.hidden = tags.length === 0;
  container.replaceChildren();
  for (const tag of tags) {
    const button = document.createElement("button");
    button.className = "tag-filter-chip";
    button.type = "button";
    button.textContent = tag;
    const isSelected = selectedFilterTags.has(tagKey(tag));
    button.setAttribute("aria-pressed", isSelected);
    button.addEventListener("click", () => {
      const key = tagKey(tag);
      if (selectedFilterTags.has(key)) {
        selectedFilterTags.delete(key);
      } else {
        selectedFilterTags.add(key);
      }
      renderTagFilter();
      applyDocumentFilters();
    });
    container.append(button);
  }
}

function applyDocumentFilters() {
  const q = documentSearch.value.trim().toLowerCase();
  let filtered = currentDocuments;

  if (q) {
    filtered = filtered.filter((doc) => doc.name.toLowerCase().includes(q));
  }

  if (selectedFilterTags.size > 0) {
    filtered = filtered.filter((doc) => {
      return (doc.tags || []).some((tag) => selectedFilterTags.has(tagKey(tag)));
    });
  }

  renderDocuments(filtered);
}

function applyDocumentType(type) {
  for (const fieldset of document.querySelectorAll("[data-document-type]")) {
    fieldset.hidden = fieldset.dataset.documentType !== type;
    fieldset.disabled = fieldset.dataset.documentType !== type;
  }
  for (const element of document.querySelectorAll("[data-for-type]")) {
    element.hidden = element.dataset.forType !== type;
  }
  // Update summary maxlength based on type
  const summaryInput = document.querySelector("#document-summary");
  if (summaryInput) {
    summaryInput.maxLength = type === "ai" ? 10000 : 1000;
  }
}

async function uploadFiles(documentId, files) {
  if (!files.length) return;
  const uploadData = new FormData();
  files.forEach((file) => uploadData.append("files", file));
  const response = await fetch(
    `/api/folders/${encodeURIComponent(currentFolderId)}/documents/${encodeURIComponent(documentId)}/files`,
    { method: "POST", body: uploadData },
  );
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || @@upload_failed@@);
}

const documentFiles = setupFileDropZone(
  "document-files",
  "document-file-drop-zone",
  "document-selected-files",
  "document-content",
);

const documentTagInput = document.querySelector("#document-tag-input");
documentTagInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === ",") {
    event.preventDefault();
    commitTagInput();
  } else if (event.key === "Backspace" && !event.target.value && documentTags.length > 0) {
    event.preventDefault();
    documentTags.pop();
    renderTagEditor();
  }
});
documentTagInput.addEventListener("change", (event) => {
  if (event.target.value) {
    commitTagInput();
  }
});

const googleDriveImportButton = document.querySelector("#google-drive-import-button");
let googleConfig = { googleClientId: "", googleApiKey: "" };
let googleApisReady = null;
let googleTokenClient = null;
let googleAccessToken = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error(format(@@script_load_failed@@, { src })));
    document.head.append(script);
  });
}

function ensureGoogleApisReady() {
  if (!googleApisReady) {
    googleApisReady = Promise.all([
      loadScript("https://accounts.google.com/gsi/client"),
      loadScript("https://apis.google.com/js/api.js").then(
        () => new Promise((resolve) => gapi.load("picker", resolve)),
      ),
    ]);
  }
  return googleApisReady;
}

function requestGoogleAccessToken() {
  return new Promise((resolve, reject) => {
    if (!googleTokenClient) {
      googleTokenClient = google.accounts.oauth2.initTokenClient({
        client_id: googleConfig.googleClientId,
        scope: "https://www.googleapis.com/auth/drive.readonly",
        callback: () => {},
      });
    }
    googleTokenClient.callback = (response) => {
      if (response.error) {
        reject(new Error(@@google_login_failed@@));
        return;
      }
      googleAccessToken = response.access_token;
      resolve(googleAccessToken);
    };
    googleTokenClient.requestAccessToken({ prompt: googleAccessToken ? "" : "consent" });
  });
}

function openGoogleDrivePicker() {
  return new Promise((resolve, reject) => {
    const picker = new google.picker.PickerBuilder()
      .addView(google.picker.ViewId.DOCS)
      .setOAuthToken(googleAccessToken)
      .setDeveloperKey(googleConfig.googleApiKey)
      .setCallback((data) => {
        if (data.action === google.picker.Action.PICKED) resolve(data.docs);
        else if (data.action === google.picker.Action.CANCEL) resolve([]);
      })
      .build();
    picker.setVisible(true);
  });
}

async function downloadGoogleDriveFile(doc) {
  const isGoogleNative = doc.mimeType.startsWith("application/vnd.google-apps");
  const url = isGoogleNative
    ? `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(doc.id)}/export?mimeType=${encodeURIComponent("application/pdf")}`
    : `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(doc.id)}?alt=media`;
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${googleAccessToken}` },
  });
  if (!response.ok) throw new Error(format(@@google_download_failed@@, { name: doc.name }));
  const blob = await response.blob();
  const name = isGoogleNative ? `${doc.name}.pdf` : doc.name;
  const type = isGoogleNative ? "application/pdf" : doc.mimeType || blob.type;
  return new File([blob], name, { type });
}

googleDriveImportButton?.addEventListener("click", async () => {
  const originalText = googleDriveImportButton.textContent;
  googleDriveImportButton.disabled = true;
  try {
    await ensureGoogleApisReady();
    await requestGoogleAccessToken();
    const docs = await openGoogleDrivePicker();
    if (!docs.length) return;
    googleDriveImportButton.textContent = @@downloading_files@@;
    const files = [];
    for (const doc of docs) files.push(await downloadGoogleDriveFile(doc));
    documentFiles.addFiles(files);
  } catch (error) {
    documentFormError.textContent = error.message || @@google_import_failed@@;
  } finally {
    googleDriveImportButton.disabled = false;
    googleDriveImportButton.textContent = originalText;
  }
});

async function loadGoogleConfig() {
  try {
    const response = await fetch("/api/config");
    googleConfig = await response.json();
  } catch {
    return;
  }
  if (googleDriveImportButton) {
    googleDriveImportButton.hidden = !(googleConfig.googleClientId && googleConfig.googleApiKey);
  }
}

function renderFolders(folders) {
  foldersElement.replaceChildren();
  emptyState.hidden = folders.length !== 0;
  folderCount.textContent = folders.length ? countLabel(folders.length, @@folders_count_one@@, @@folders_count_other@@) : "";

  for (const folder of folders) {
    const card = document.createElement("button");
    card.className = "folder-card";
    card.type = "button";
    card.dataset.folderId = folder.id;

    const icon = document.createElement("span");
    icon.className = "folder-icon";
    icon.textContent = "⌑";
    icon.setAttribute("aria-hidden", "true");

    const name = document.createElement("strong");
    name.textContent = folder.name;
    name.dir = "auto";

    const description = document.createElement("span");
    description.className = "description";
    description.dir = "auto";
    description.textContent = folder.description || @@no_description@@;

    const updated = document.createElement("span");
    updated.className = "updated";
    updated.textContent = format(@@updated@@, { date: dateFormatter.format(new Date(folder.updated_at)) });

    card.append(icon, name, description, updated);
    card.addEventListener("click", () => openFolder(folder.id));
    foldersElement.append(card);
  }
  renderSidebarFolders(folders);
}

function renderSidebarFolders(folders) {
  if (!sidebarFolders) return;
  sidebarFolders.replaceChildren();
  folders.forEach(folder => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.className = "sidebar-folder-link";
    a.href = "#folder/" + folder.id;
    a.setAttribute("data-folder-id", folder.id);
    a.setAttribute("dir", "auto");
    a.textContent = folder.name;
    li.appendChild(a);
    sidebarFolders.appendChild(li);
  });
  setActiveSidebarFolder(currentFolderId);
}

function setActiveSidebarFolder(folderId) {
  if (!sidebarFolders) return;
  sidebarFolders.querySelectorAll(".sidebar-folder-link").forEach(link => {
    if (folderId && link.getAttribute("data-folder-id") === folderId) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  });
}

function setSidebarOpen(open) {
  document.body.classList.toggle("sidebar-open", open);
  if (sidebarToggle) sidebarToggle.setAttribute("aria-expanded", String(open));
}

const documentDialogEyebrow = document.querySelector("#document-dialog-eyebrow");
const documentDialogTitle = document.querySelector("#document-dialog-title");
const documentSubmitButton = documentForm.querySelector('button[type="submit"]');

function renderPrintExport(folderName, documents) {
  document.querySelector("#print-export-title").textContent = folderName;
  document.querySelector("#print-export-rows").replaceChildren(...documents.map((documentItem) => {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.dir = "auto";
    name.textContent = documentItem.name;
    const date = document.createElement("td");
    date.textContent = dateFormatter.format(new Date(`${documentItem.date}T00:00:00`));
    row.append(name, date);
    return row;
  }));
}

function renderPdfViewer(attachment, url) {
  const viewer = document.createElement("div");
  viewer.className = "pdf-viewer";
  viewer.dataset.fileId = String(attachment.id);
  const bar = document.createElement("div");
  bar.className = "pdf-viewer-bar";
  const filename = document.createElement("span");
  filename.textContent = attachment.name;
  filename.dir = "auto";
  const openLink = document.createElement("a");
  openLink.href = url;
  openLink.target = "_blank";
  openLink.rel = "noopener";
  openLink.textContent = @@open_pdf_new_tab@@;
  const closeButton = document.createElement("button");
  closeButton.className = "secondary-button small-button";
  closeButton.type = "button";
  closeButton.textContent = @@close_pdf@@;
  closeButton.addEventListener("click", () => {
    const container = viewer.parentElement;
    viewer.remove();
    container?.querySelector('.attachment-pdf[aria-expanded="true"]')?.setAttribute("aria-expanded", "false");
  });
  bar.append(filename, openLink, closeButton);
  const frame = document.createElement("iframe");
  frame.className = "pdf-frame";
  frame.src = url;
  frame.title = format(@@pdf_viewer_title@@, { name: attachment.name });
  viewer.append(bar, frame);
  return viewer;
}

function renderDocuments(documents) {
  const openIds = new Set(
    [...documentsElement.querySelectorAll("details.document-item[open]")].map(
      (details) => details.dataset.documentId,
    ),
  );
  documentsElement.replaceChildren();
  documentsEmptyState.hidden = documents.length !== 0;
  document.querySelector("#document-count").textContent =
    documents.length ? countLabel(documents.length, @@documents_count_one@@, @@documents_count_other@@) : "";

  for (const documentItem of documents) {
    const item = document.createElement("details");
    item.className = "document-item";
    item.dataset.documentId = documentItem.id;
    if (openIds.has(documentItem.id)) item.open = true;
    const summary = document.createElement("summary");
    const summaryText = document.createElement("span");
    const title = document.createElement("h3");
    title.textContent = documentItem.name;
    title.dir = "auto";

    // Add AI badge for AI documents
    if (documentItem.type === "ai") {
      const badge = document.createElement("span");
      badge.className = "document-type-badge";
      badge.textContent = @@document_type_ai@@;
      title.append(" ", badge);
    }

    const documentDate = document.createElement("span");
    const formattedDate = dateFormatter.format(new Date(`${documentItem.date}T00:00:00`));
    let dateText = documentItem.time
      ? `${formattedDate}, ${documentItem.time}`
      : formattedDate;
    if (documentItem.type === "ai" && documentItem.provider) {
      dateText += ` · ${documentItem.provider}`;
    }
    documentDate.textContent = dateText;
    summaryText.append(title, documentDate);
    if (documentItem.tags?.length) {
      const tagsContainer = document.createElement("span");
      tagsContainer.className = "document-tags";
      for (const tag of documentItem.tags) {
        const tagChip = document.createElement("span");
        tagChip.className = "tag-chip";
        tagChip.textContent = tag;
        tagsContainer.append(tagChip);
      }
      summaryText.append(tagsContainer);
    }
    const marker = document.createElement("span");
    marker.className = "document-marker";
    marker.textContent = "⌄";
    summary.append(summaryText, marker);
    const body = document.createElement("div");
    body.className = "document-body";

    const editActions = document.createElement("div");
    editActions.className = "document-actions";

    const editButton = document.createElement("button");
    editButton.className = "secondary-button small-button";
    editButton.type = "button";
    editButton.textContent = @@edit_document@@;
    editButton.addEventListener("click", () => {
      currentDocumentId = documentItem.id;
      documentDialogEyebrow.textContent = @@edit_document_eyebrow@@;
      documentDialogTitle.textContent = @@edit_document_title@@;
      documentSubmitButton.textContent = @@save_changes@@;

      const docType = documentItem.type || "regular";
      documentTypeSelect.value = docType;
      documentTypeSelect.disabled = true;  // Type is immutable after creation
      importAiThreadButton.hidden = true;
      applyDocumentType(docType);

      const dateInput = document.querySelector("#document-date");
      const timeInput = document.querySelector("#document-time");
      dateInput.value = documentItem.date;
      timeInput.value = documentItem.time || "";
      document.querySelector("#document-name").value = documentItem.name;

      if (docType === "regular") {
        dateInput.disabled = false;
        timeInput.disabled = false;
        document.querySelector("#document-description").value = documentItem.description || "";
        document.querySelector("#document-content").value = documentItem.content || "";
      } else {
        // AI document
        document.querySelector("#document-provider").value = documentItem.provider || "";
        document.querySelector("#document-transcript").value = documentItem.content || "";
        // Lock date, time, and transcript for imported documents
        const lockedHint = document.querySelector("#imported-transcript-locked-hint");
        if (lockedHint) {
          lockedHint.hidden = !documentItem.source_url;
          if (documentItem.source_url) {
            document.querySelector("#document-transcript").disabled = true;
            dateInput.disabled = true;
            timeInput.disabled = true;
          } else {
            document.querySelector("#document-transcript").disabled = false;
            dateInput.disabled = false;
            timeInput.disabled = false;
          }
        }
      }

      document.querySelector("#document-summary").value = documentItem.summary || "";
      documentTags = [...(documentItem.tags || [])];
      renderTagEditor();
      loadTagSuggestions();

      documentFormError.textContent = "";
      documentFiles.clear();
      documentDialog.showModal();
    });

    editActions.append(editButton);

    const linkButton = document.createElement("button");
    linkButton.className = "secondary-button small-button";
    linkButton.type = "button";
    linkButton.textContent = @@add_link@@;
    linkButton.addEventListener("click", () => {
      openLinkDialog(documentItem);
    });
    editActions.append(linkButton);

    if (documentItem.whatsapp_chat?.messages?.length) {
      const chatToggle = document.createElement("button");
      chatToggle.className = "secondary-button small-button";
      chatToggle.type = "button";
      chatToggle.textContent = @@show_whatsapp@@;
      let chatElement = null;
      chatToggle.addEventListener("click", () => {
        if (chatElement) {
          chatElement.remove();
          chatElement = null;
          chatToggle.textContent = @@show_whatsapp@@;
        } else {
          chatElement = renderWhatsAppChat(documentItem.whatsapp_chat);
          body.append(chatElement);
          chatToggle.textContent = @@hide_whatsapp@@;
        }
      });
      editActions.append(chatToggle);
    }

    body.append(editActions);

    if (documentItem.description) {
      const description = document.createElement("p");
      description.className = "document-description";
      description.dir = "auto";
      description.textContent = documentItem.description;
      body.append(description);
    }
    if (documentItem.summary) {
      const thoughtsSummary = document.createElement("p");
      thoughtsSummary.className = "document-summary";
      thoughtsSummary.dir = "auto";
      thoughtsSummary.textContent = documentItem.summary;
      body.append(thoughtsSummary);
    }

    // AI-specific fields
    if (documentItem.type === "ai") {
      if (documentItem.content) {
        const transcriptSection = document.createElement("section");
        transcriptSection.className = "ai-thread-content";
        const transcriptHeading = document.createElement("h4");
        transcriptHeading.textContent = @@ai_thread_transcript@@;
        const transcriptContent = document.createElement("pre");
        transcriptContent.textContent = documentItem.content;
        transcriptContent.dir = "auto";
        transcriptSection.append(transcriptHeading, transcriptContent);
        body.append(transcriptSection);
      }
      if (documentItem.source_url) {
        const source = document.createElement("p");
        source.className = "ai-thread-source";
        const sourceLabel = document.createElement("span");
        sourceLabel.textContent = `${@@share_source@@} `;
        const link = document.createElement("a");
        link.href = documentItem.source_url;
        link.textContent = documentItem.source_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        source.append(sourceLabel, link);
        body.append(source);
      }
      if (documentItem.transcript_sha256) {
        const hash = document.createElement("p");
        hash.className = "ai-thread-source";
        hash.textContent = format(@@share_hash@@, { hash: documentItem.transcript_sha256 });
        body.append(hash);
      }
    }

    if (documentItem.files?.length) {
      const files = document.createElement("div");
      files.className = "attachment-grid";
      for (const attachment of documentItem.files) {
        const url = `/api/folders/${encodeURIComponent(currentFolderId)}/documents/${encodeURIComponent(documentItem.id)}/files/${encodeURIComponent(attachment.id)}`;
        const item = document.createElement("div");
        item.className = "attachment-item";
        const link = document.createElement("a");
        link.className = "attachment";
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener";
        if (attachment.content_type.startsWith("image/")) {
          const image = document.createElement("img");
          image.src = url;
          image.alt = attachment.name;
          link.append(image);
        }
        if (attachment.content_type === "application/pdf") {
          link.classList.add("attachment-pdf");
          const badge = document.createElement("span");
          badge.className = "attachment-pdf-badge";
          badge.setAttribute("aria-hidden", "true");
          badge.textContent = "PDF";
          link.append(badge);
          link.title = @@show_pdf@@;
          link.setAttribute("aria-expanded", "false");
          link.addEventListener("click", (event) => {
            if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            const existing = body.querySelector(".pdf-viewer");
            const isOpen = existing?.dataset.fileId === String(attachment.id);
            existing?.remove();
            for (const other of files.querySelectorAll('.attachment-pdf[aria-expanded="true"]')) {
              other.setAttribute("aria-expanded", "false");
            }
            if (isOpen) return;
            files.after(renderPdfViewer(attachment, url));
            link.setAttribute("aria-expanded", "true");
          });
        }
        const filename = document.createElement("span");
        filename.textContent = attachment.name;
        filename.dir = "auto";
        link.append(filename);
        const removeButton = document.createElement("button");
        removeButton.className = "attachment-remove";
        removeButton.type = "button";
        removeButton.textContent = "×";
        removeButton.title = @@remove_file@@;
        removeButton.setAttribute("aria-label", @@remove_file@@);
        removeButton.addEventListener("click", async () => {
          if (!confirm(format(@@confirm_delete_file@@, { name: attachment.name }))) return;
          removeButton.disabled = true;
          const response = await fetch(url, { method: "DELETE" });
          if (!response.ok) {
            removeButton.disabled = false;
            alert(@@delete_file_failed@@);
            return;
          }
          await openFolder(currentFolderId, false);
        });
        item.append(link, removeButton);
        files.append(item);
      }
      body.append(files);
    }
    if (documentItem.links?.length) {
      body.append(renderDocumentLinks(documentItem));
    }
    item.append(summary, body);
    documentsElement.append(item);
  }
}

function renderWhatsAppChat(chat) {
  const wrapper = document.createElement("div");
  const conversation = document.createElement("div");
  conversation.className = "whatsapp-chat";
  let previousDate = null;

  for (const message of chat.messages) {
    if (message.date !== previousDate) {
      const dateSeparator = document.createElement("div");
      dateSeparator.className = "chat-date";
      dateSeparator.textContent = message.date;
      conversation.append(dateSeparator);
      previousDate = message.date;
    }
    if (message.system) {
      const system = document.createElement("div");
      system.className = "chat-system-message";
      system.textContent = message.content;
      conversation.append(system);
      continue;
    }
    const row = document.createElement("div");
    const ownerName = chat.me || "Liora Milbaum";
    row.className = `chat-message-row ${message.sender === ownerName ? "mine" : "theirs"}`;
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    const sender = document.createElement("strong");
    sender.textContent = message.sender;
    sender.dir = "auto";
    bubble.append(sender);
    if (message.reply_to) {
      const quote = document.createElement("div");
      quote.className = "chat-reply-quote";
      const quoteSender = document.createElement("strong");
      quoteSender.textContent = message.reply_to.sender;
      const quoteText = document.createElement("span");
      quoteText.textContent = message.reply_to.content;
      quoteText.dir = "auto";
      quote.append(quoteSender, quoteText);
      bubble.append(quote);
    }
    const content = document.createElement("p");
    content.dir = "auto";
    appendLinkifiedText(content, message.content);
    const time = document.createElement("time");
    time.textContent = message.time;
    bubble.append(content, time);
    row.append(bubble);
    conversation.append(row);
  }
  wrapper.append(conversation);
  return wrapper;
}

function appendLinkifiedText(element, text) {
  const linkPattern = /(?:https?:\/\/|www\.)[^\s<>]+/gi;
  let cursor = 0;
  for (const match of text.matchAll(linkPattern)) {
    element.append(document.createTextNode(text.slice(cursor, match.index)));
    const trailingMatch = match[0].match(/[.,!?;:)}\]]+$/);
    const trailing = trailingMatch ? trailingMatch[0] : "";
    const linkText = trailing ? match[0].slice(0, -trailing.length) : match[0];
    const link = document.createElement("a");
    link.href = linkText.startsWith("www.") ? `https://${linkText}` : linkText;
    link.textContent = linkText;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    element.append(link);
    if (trailing) element.append(document.createTextNode(trailing));
    cursor = match.index + match[0].length;
  }
  element.append(document.createTextNode(text.slice(cursor)));
}

async function loadLinkTypes(selectId) {
  const response = await fetch("/api/link-types");
  if (!response.ok) {
    throw new Error("Failed to load link types");
  }
  const types = await response.json();
  linkTypeSelect.replaceChildren(document.createElement("option"));
  linkTypeSelect.options[0].value = "";
  linkTypeSelect.options[0].textContent = @@choose_link_type@@;
  for (const type of types) {
    const option = document.createElement("option");
    option.value = type.id;
    option.textContent = type.name;
    linkTypeSelect.append(option);
  }
  if (selectId) {
    linkTypeSelect.value = selectId;
  }
  linkTypesEmptyHint.hidden = types.length > 0;
}

async function loadLinkTargets(excludeId) {
  const response = await fetch("/api/folders");
  if (!response.ok) {
    throw new Error("Failed to load folders");
  }
  const folders = await response.json();
  linkTargetSelect.replaceChildren(document.createElement("option"));
  linkTargetSelect.options[0].value = "";
  linkTargetSelect.options[0].textContent = @@choose_link_target@@;

  for (const folder of folders) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = folder.name;
    if (folder.id === currentFolderId) {
      linkTargetSelect.insertBefore(optgroup, linkTargetSelect.firstChild.nextSibling);
    } else {
      linkTargetSelect.append(optgroup);
    }
    for (const doc of folder.documents) {
      if (doc.id === excludeId) continue;
      const option = document.createElement("option");
      option.value = doc.id;
      const dateStr = new Date(doc.date + "T00:00:00").toLocaleDateString(@@date_locale@@);
      option.textContent = `${doc.name} · ${dateStr}`;
      optgroup.append(option);
    }
  }
}

function openLinkDialog(documentItem) {
  linkSourceDocumentId = documentItem.id;
  linkForm.reset();
  linkFormError.textContent = "";
  loadLinkTypes();
  loadLinkTargets(documentItem.id);
  linkDialog.showModal();
}

function renderDocumentLinks(documentItem) {
  const section = document.createElement("section");
  section.className = "document-links";

  const outgoing = documentItem.links?.filter(l => l.direction === "outgoing") || [];
  const incoming = documentItem.links?.filter(l => l.direction === "incoming") || [];

  if (outgoing.length > 0) {
    const heading = document.createElement("h4");
    heading.textContent = @@links_outgoing@@;
    section.append(heading);

    const ul = document.createElement("ul");
    for (const link of outgoing) {
      const li = document.createElement("li");
      li.className = "link-item";

      const typeChip = document.createElement("span");
      typeChip.className = "tag-chip";
      typeChip.textContent = link.type;

      const button = document.createElement("button");
      button.className = "link-target";
      button.type = "button";
      button.textContent = link.document.name;
      if (link.document.folder_id !== currentFolderId) {
        button.textContent += ` · ${format(@@link_in_folder@@, {folder: link.document.folder_name})}`;
      }
      button.addEventListener("click", () => {
        revealDocument(link.document.folder_id, link.document.id);
      });

      const removeButton = document.createElement("button");
      removeButton.className = "link-remove";
      removeButton.type = "button";
      removeButton.textContent = "×";
      removeButton.setAttribute("aria-label", format(@@remove_link@@, {name: link.document.name}));
      removeButton.title = format(@@remove_link@@, {name: link.document.name});
      removeButton.addEventListener("click", async () => {
        if (!confirm(format(@@confirm_remove_link@@, {name: link.document.name}))) return;
        removeButton.disabled = true;
        const response = await fetch(
          `/api/folders/${encodeURIComponent(currentFolderId)}/documents/${encodeURIComponent(documentItem.id)}/links/${encodeURIComponent(link.id)}`,
          {method: "DELETE"}
        );
        if (!response.ok) {
          removeButton.disabled = false;
          alert(@@remove_link_failed@@);
          return;
        }
        await openFolder(currentFolderId, false);
      });

      li.append(typeChip, button, removeButton);
      ul.append(li);
    }
    section.append(ul);
  }

  if (incoming.length > 0) {
    const heading = document.createElement("h4");
    heading.textContent = @@links_incoming@@;
    section.append(heading);

    const ul = document.createElement("ul");
    for (const link of incoming) {
      const li = document.createElement("li");
      li.className = "link-item";

      const typeChip = document.createElement("span");
      typeChip.className = "tag-chip";
      typeChip.textContent = link.type;

      const button = document.createElement("button");
      button.className = "link-target";
      button.type = "button";
      button.textContent = link.document.name;
      if (link.document.folder_id !== currentFolderId) {
        button.textContent += ` · ${format(@@link_in_folder@@, {folder: link.document.folder_name})}`;
      }
      button.addEventListener("click", () => {
        revealDocument(link.document.folder_id, link.document.id);
      });

      const removeButton = document.createElement("button");
      removeButton.className = "link-remove";
      removeButton.type = "button";
      removeButton.textContent = "×";
      removeButton.setAttribute("aria-label", format(@@remove_link@@, {name: link.document.name}));
      removeButton.title = format(@@remove_link@@, {name: link.document.name});
      removeButton.addEventListener("click", async () => {
        if (!confirm(format(@@confirm_remove_link@@, {name: link.document.name}))) return;
        removeButton.disabled = true;
        const response = await fetch(
          `/api/folders/${encodeURIComponent(currentFolderId)}/documents/${encodeURIComponent(documentItem.id)}/links/${encodeURIComponent(link.id)}`,
          {method: "DELETE"}
        );
        if (!response.ok) {
          removeButton.disabled = false;
          alert(@@remove_link_failed@@);
          return;
        }
        await openFolder(currentFolderId, false);
      });

      li.append(typeChip, button, removeButton);
      ul.append(li);
    }
    section.append(ul);
  }

  return section;
}

async function revealDocument(folderId, documentId) {
  if (folderId !== currentFolderId) {
    await openFolder(folderId);
  }
  const details = document.querySelector(`details[data-document-id="${CSS.escape(documentId)}"]`);
  if (details) {
    details.open = true;
    details.scrollIntoView({block: "start"});
  }
}

async function openFolder(folderId, updateHash = true) {
  const response = await fetch(`/api/folders/${encodeURIComponent(folderId)}`);
  if (!response.ok) {
    window.location.hash = "";
    throw new Error("Folder not found");
  }
  const folder = await response.json();
  currentFolderId = folder.id;
  setActiveSidebarFolder(currentFolderId);
  currentDocuments = folder.documents || [];
  exportDocumentsButton.disabled = currentDocuments.length === 0;
  documentSearch.value = "";
  selectedFilterTags.clear();
  document.querySelector("#current-folder-name").textContent = folder.name;
  document.querySelector("#current-folder-description").textContent =
    folder.description || @@no_description@@;
  renderTagFilter();
  renderDocuments(currentDocuments);
  homeView.hidden = true;
  folderView.hidden = false;
  document.title = format(@@folder_title@@, { name: folder.name });
  if (updateHash) history.pushState(null, "", `#folder/${folder.id}`);
}

function showHome(updateHash = true) {
  currentFolderId = null;
  setActiveSidebarFolder(null);
  currentDocuments = [];
  documentSearch.value = "";
  selectedFilterTags.clear();
  folderView.hidden = true;
  homeView.hidden = false;
  document.title = @@app_title@@;
  if (updateHash) history.pushState(null, "", window.location.pathname);
}

async function loadFolders() {
  const response = await fetch("/api/folders");
  renderFolders(await response.json());
}

document.querySelector("#new-folder-button").addEventListener("click", () => dialog.showModal());
document.querySelector("#close-dialog").addEventListener("click", () => dialog.close());
document.querySelector("#cancel-dialog").addEventListener("click", () => dialog.close());
document.querySelector("#back-button").addEventListener("click", () => showHome());
exportDocumentsButton.addEventListener("click", () => {
  renderPrintExport(document.querySelector("#current-folder-name").textContent, currentDocuments);
  document.querySelector("#print-export").setAttribute("aria-hidden", "false");
  window.print();
});
document.querySelector("#new-document-button").addEventListener("click", () => {
  currentDocumentId = null;
  documentDialogEyebrow.textContent = @@new_document_eyebrow@@;
  documentDialogTitle.textContent = @@new_document_title@@;
  documentSubmitButton.textContent = @@save_document@@;
  documentForm.reset();
  documentFiles.clear();
  documentFormError.textContent = "";
  documentTags = [];
  renderTagEditor();
  loadTagSuggestions();
  document.querySelector("#document-date").value = new Date().toISOString().slice(0, 10);
  document.querySelector("#document-date").disabled = false;
  document.querySelector("#document-time").disabled = false;
  documentTypeSelect.value = "regular";
  documentTypeSelect.disabled = false;
  applyDocumentType("regular");
  importAiThreadButton.hidden = false;
  documentDialog.showModal();
});
document.querySelector("#close-document-dialog").addEventListener("click", () => documentDialog.close());
document.querySelector("#cancel-document-dialog").addEventListener("click", () => documentDialog.close());
importAiThreadButton.addEventListener("click", () => {
  documentDialog.close();
  aiShareForm.reset();
  aiShareConfirmForm.reset();
  document.querySelector("#ai-share-date").readOnly = false;
  document.querySelector("#ai-share-time").readOnly = false;
  aiSharePreviewId = null;
  aiShareError.textContent = "";
  aiShareConfirmError.textContent = "";
  aiShareForm.hidden = false;
  aiShareConfirmForm.hidden = true;
  aiShareDialog.showModal();
});
document.querySelector("#close-ai-share-dialog").addEventListener("click", () => aiShareDialog.close());
document.querySelector("#cancel-ai-share-dialog").addEventListener("click", () => aiShareDialog.close());
document.querySelector("#ai-share-back").addEventListener("click", () => {
  aiSharePreviewId = null;
  aiShareForm.hidden = false;
  aiShareConfirmForm.hidden = true;
});
aiShareDialog.addEventListener("close", () => { aiSharePreviewId = null; });
aiShareDialog.addEventListener("click", (event) => {
  if (event.target === aiShareDialog) aiShareDialog.close();
});
aiShareForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  aiShareError.textContent = "";
  const button = document.querySelector("#ai-share-preview-button");
  button.disabled = true;
  try {
    const response = await fetch(`/api/folders/${encodeURIComponent(currentFolderId)}/documents/share-preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: (() => { const u = document.querySelector("#ai-share-url").value.trim(); return u.startsWith("https://") ? u : `https://${u}`; })() }),
    });
    const result = await response.json();
    if (!response.ok) {
      aiShareError.textContent = shareErrors[result.error] || @@share_unsupported_format@@;
      return;
    }
    aiSharePreviewId = result.preview_id;
    document.querySelector("#ai-share-preview-title").textContent = result.title;
    document.querySelector("#ai-share-message-count").textContent = format(@@share_messages@@, { count: result.message_count });
    document.querySelector("#ai-share-warnings").textContent = [
      result.warnings.includes("non_visible_omitted") ? @@share_non_visible_omitted@@ : "",
      result.warnings.includes("non_text_omitted") ? @@share_non_text_omitted@@ : "",
      result.warnings.includes("timestamp_unavailable") ? @@share_timestamp_unavailable@@ : "",
    ].filter(Boolean).join(" ");
    document.querySelector("#ai-share-transcript").textContent = result.transcript;
    const sourceDateTime = localDateTimeInputs(result.source_first_message_at);
    const dateInput = document.querySelector("#ai-share-date");
    const timeInput = document.querySelector("#ai-share-time");
    dateInput.value = sourceDateTime?.date || "";
    timeInput.value = sourceDateTime?.time || "";
    dateInput.readOnly = !!sourceDateTime;
    timeInput.readOnly = !!sourceDateTime;
    aiShareForm.hidden = true;
    aiShareConfirmForm.hidden = false;
  } catch {
    aiShareError.textContent = @@connect_failed@@;
  } finally {
    button.disabled = false;
  }
});
aiShareConfirmForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  aiShareConfirmError.textContent = "";
  const button = aiShareConfirmForm.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const response = await fetch(`/api/folders/${encodeURIComponent(currentFolderId)}/documents/share-confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        preview_id: aiSharePreviewId,
        conversation_date: document.querySelector("#ai-share-date").value,
        conversation_time: document.querySelector("#ai-share-time").value,
        summary: document.querySelector("#ai-share-summary").value,
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      aiShareConfirmError.textContent = shareErrors[result.error] || @@save_failed@@;
      return;
    }
    aiShareDialog.close();
    await openFolder(currentFolderId, false);
  } catch {
    aiShareConfirmError.textContent = @@connect_failed@@;
  } finally {
    button.disabled = false;
  }
});

dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});

documentDialog.addEventListener("click", (event) => {
  if (event.target === documentDialog) documentDialog.close();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.textContent = "";
  const data = new FormData(form);
  const response = await fetch("/api/folders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: data.get("name"), description: data.get("description") }),
  });
  const result = await response.json();
  if (!response.ok) {
    formError.textContent = result.error;
    return;
  }
  form.reset();
  dialog.close();
  await loadFolders();
});

documentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  documentFormError.textContent = "";
  commitTagInput();
  const submitButton = documentForm.querySelector('button[type="submit"]');
  const originalButtonText = submitButton.textContent;
  submitButton.disabled = true;
  submitButton.textContent = @@saving@@;
  const data = new FormData(documentForm);
  try {
    const isEdit = !!currentDocumentId;
    const url = isEdit
      ? `/api/folders/${encodeURIComponent(currentFolderId)}/documents/${encodeURIComponent(currentDocumentId)}`
      : `/api/folders/${encodeURIComponent(currentFolderId)}/documents`;
    const method = isEdit ? "PATCH" : "POST";

    const documentType = documentTypeSelect.value || "regular";
    const payload = {
      date: data.get("date"),
      time: data.get("time"),
      name: data.get("name"),
      summary: data.get("summary"),
      tags: documentTags,
    };

    // Include type on POST only (immutable after creation)
    if (!isEdit) {
      payload.type = documentType;
    }

    if (documentType === "regular") {
      payload.description = data.get("description");
      payload.content = data.get("content");
    } else {
      // AI document
      payload.provider = data.get("provider");
      payload.content = data.get("content");  // Use content for transcript
    }

    const response = await fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(Object.entries(payload).filter(([, v]) => v !== null))),
    });
    const contentType = response.headers.get("Content-Type") || "";
    const result = contentType.includes("application/json")
      ? await response.json()
      : null;
    if (!response.ok) {
      documentFormError.textContent =
        result?.error || @@save_failed@@;
      return;
    }

    // Only allow file uploads for regular documents
    let selectedFiles = [];
    if (documentType === "regular") {
      selectedFiles = data.getAll("files").filter((file) => file.size > 0);
    }

    // Retry an upload against the saved document instead of creating a duplicate.
    currentDocumentId = result.id;
    await uploadFiles(result.id, selectedFiles);
    documentForm.reset();
    documentFiles.clear();
    documentDialog.close();
    await openFolder(currentFolderId, false);
  } catch (error) {
    documentFormError.textContent =
      error.message || @@connect_failed@@;
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = originalButtonText;
  }
});

closeLinkDialogButton.addEventListener("click", () => linkDialog.close());
cancelLinkDialogButton.addEventListener("click", () => linkDialog.close());
linkDialog.addEventListener("close", () => {
  linkSourceDocumentId = null;
});
linkDialog.addEventListener("click", (event) => {
  if (event.target === linkDialog) linkDialog.close();
});

addLinkTypeButton.addEventListener("click", async () => {
  const name = newLinkTypeInput.value.trim();
  if (!name) return;

  const response = await fetch("/api/link-types", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name}),
  });

  if (!response.ok) {
    const result = await response.json();
    linkFormError.textContent = linkErrors[result.error] || @@link_error_invalid_type@@;
    return;
  }

  const result = await response.json();
  newLinkTypeInput.value = "";
  linkFormError.textContent = "";
  await loadLinkTypes(result.id);
});

newLinkTypeInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addLinkTypeButton.click();
  }
});

linkForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  linkFormError.textContent = "";

  const submitButton = linkForm.querySelector('button[type="submit"]');
  const originalButtonText = submitButton.textContent;
  submitButton.disabled = true;
  submitButton.textContent = @@saving@@;

  try {
    const response = await fetch(
      `/api/folders/${encodeURIComponent(currentFolderId)}/documents/${encodeURIComponent(linkSourceDocumentId)}/links`,
      {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          target_document_id: linkForm.elements.target_document_id.value,
          link_type_id: linkForm.elements.link_type_id.value,
        }),
      }
    );

    if (!response.ok) {
      const result = await response.json();
      linkFormError.textContent = linkErrors[result.error] || @@save_failed@@;
      return;
    }

    linkDialog.close();
    await openFolder(currentFolderId, false);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = originalButtonText;
  }
});

documentSearch.addEventListener("input", applyDocumentFilters);

documentTypeSelect.addEventListener("change", (event) => {
  applyDocumentType(event.target.value);
});

// Sidebar open/close
if (sidebarToggle) {
  sidebarToggle.addEventListener("click", () => setSidebarOpen(true));
}
const sidebarCloseBtn = document.getElementById("sidebar-close");
if (sidebarCloseBtn) {
  sidebarCloseBtn.addEventListener("click", () => setSidebarOpen(false));
}
if (sidebarScrim) {
  sidebarScrim.addEventListener("click", () => setSidebarOpen(false));
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.body.classList.contains("sidebar-open") && !document.querySelector("dialog[open]")) {
    setSidebarOpen(false);
  }
});
if (sidebarFolders) {
  sidebarFolders.addEventListener("click", (e) => {
    if (e.target.closest(".sidebar-folder-link")) setSidebarOpen(false);
  });
}
const newFolderSidebarBtn = document.getElementById("new-folder-button");
if (newFolderSidebarBtn) {
  newFolderSidebarBtn.addEventListener("click", () => setSidebarOpen(false));
}
if (topbarNewFolderButton) {
  topbarNewFolderButton.addEventListener("click", () => dialog.showModal());
}

async function start() {
  await loadFolders();
  loadGoogleConfig();
  const folderMatch = window.location.hash.match(/^#folder\/(.+)$/);
  if (folderMatch) await openFolder(folderMatch[1], false);
}

window.addEventListener("hashchange", () => {
  const folderMatch = window.location.hash.match(/^#folder\/(.+)$/);
  if (folderMatch) openFolder(folderMatch[1], false);
  else showHome(false);
});

start().catch(() => {
  foldersElement.textContent = @@load_folders_failed@@;
});
