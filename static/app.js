/**
 * Redaktor AI — Frontend Logic
 * Komunikacja z Flask API + zarządzanie interfejsem.
 */

// ===== STATE =====

const state = {
    totalPages: 0,
    currentPage: 1,
    filename: null,
    fileType: null,
    projectName: null,
    processing: false,
};

// ===== UPLOAD =====

const uploadZone = document.getElementById('upload-zone');
const fileInput = document.getElementById('file-input');

uploadZone.addEventListener('click', () => fileInput.click());

uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('drag-over');
});

uploadZone.addEventListener('dragleave', () => {
    uploadZone.classList.remove('drag-over');
});

uploadZone.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadZone.classList.remove('drag-over');
    if (e.dataTransfer.files.length > 0) {
        uploadFile(e.dataTransfer.files[0]);
    }
});

fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
        uploadFile(fileInput.files[0]);
    }
});

async function uploadFile(file) {
    const uploadSection = document.getElementById('upload-section');
    const progress = document.getElementById('upload-progress');
    const zone = document.getElementById('upload-zone');

    zone.classList.add('hidden');
    progress.classList.remove('hidden');

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/upload', { method: 'POST', body: formData });
        const data = await resp.json();

        if (data.error) {
            showToast(data.error, 'error');
            zone.classList.remove('hidden');
            progress.classList.add('hidden');
            return;
        }

        state.totalPages = data.total_pages;
        state.filename = data.filename;
        state.fileType = data.file_type;
        state.projectName = data.project_name;
        state.currentPage = 1;

        initWorkspace();
        showToast(`Załadowano: ${data.filename} (${data.total_pages} stron)`, 'success');

    } catch (err) {
        showToast('Błąd uploadu: ' + err.message, 'error');
        zone.classList.remove('hidden');
        progress.classList.add('hidden');
    }
}

// ===== WORKSPACE INIT =====

function initWorkspace() {
    document.getElementById('upload-section').classList.add('hidden');
    document.getElementById('workspace').classList.remove('hidden');

    // Doc info
    document.getElementById('doc-info').textContent =
        `${state.filename} · ${state.fileType.toUpperCase()} · ${state.totalPages} str.`;
    document.getElementById('status-text').textContent =
        `${state.projectName}`;

    // Slider
    const slider = document.getElementById('page-slider');
    slider.max = state.totalPages;
    slider.value = 1;
    slider.addEventListener('input', (e) => {
        goToPage(parseInt(e.target.value));
    });

    // Range controls
    document.getElementById('range-end').value = state.totalPages;
    document.getElementById('range-end').max = state.totalPages;
    document.getElementById('range-start').max = state.totalPages;

    // Mode switcher
    document.getElementById('process-mode').addEventListener('change', (e) => {
        document.getElementById('range-controls').classList.toggle('hidden', e.target.value !== 'range');
        document.getElementById('article-controls').classList.toggle('hidden', e.target.value !== 'article');
    });

    loadPage(1);
}

// ===== NAVIGATION =====

function navigatePage(delta) {
    const newPage = state.currentPage + delta;
    if (newPage >= 1 && newPage <= state.totalPages) {
        goToPage(newPage);
    }
}

function goToPage(n) {
    state.currentPage = n;
    document.getElementById('page-slider').value = n;
    loadPage(n);
}

async function loadPage(pageNum) {
    state.currentPage = pageNum;
    updateNavigation();

    const originalContent = document.getElementById('original-content');
    const processedContent = document.getElementById('processed-content');
    const rawTextSection = document.getElementById('raw-text-toggle');
    const rawText = document.getElementById('raw-text');
    const resultBadge = document.getElementById('result-type');
    const actionButtons = document.getElementById('action-buttons');
    const metaResult = document.getElementById('meta-result');
    const seoResult = document.getElementById('seo-result');

    // Reset
    resultBadge.classList.add('hidden');
    actionButtons.classList.add('hidden');
    metaResult.classList.add('hidden');
    seoResult.classList.add('hidden');

    // Load original
    if (state.fileType === 'pdf') {
        originalContent.innerHTML = `<img src="/page/${pageNum}/preview?t=${Date.now()}" alt="Strona ${pageNum}" loading="lazy">`;
    } else {
        originalContent.innerHTML = `<p class="placeholder-text">Podgląd niedostępny dla ${state.fileType.toUpperCase()}</p>`;
    }

    // Load raw text
    try {
        const textResp = await fetch(`/page/${pageNum}/text`);
        const textData = await textResp.json();
        rawText.textContent = textData.text || '';
        rawTextSection.classList.remove('hidden');
    } catch {
        rawTextSection.classList.add('hidden');
    }

    // Load result
    try {
        const resultResp = await fetch(`/page/${pageNum}/result`);
        const resultData = await resultResp.json();

        if (resultData.processed) {
            const type = resultData.type || 'nieznany';

            resultBadge.textContent = type.toUpperCase();
            resultBadge.className = `result-badge type-${type}`;
            resultBadge.classList.remove('hidden');

            processedContent.innerHTML = resultData.formatted_content || '<p>Brak treści.</p>';

            // Group info
            if (resultData.group_pages && resultData.group_pages.length > 1) {
                const groupInfo = document.createElement('div');
                groupInfo.style.cssText = 'background: var(--bg-card); padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 0.85rem; color: var(--text-secondary);';
                groupInfo.textContent = `📑 Artykuł ze stron: ${resultData.group_pages.join(', ')}`;
                processedContent.insertBefore(groupInfo, processedContent.firstChild);
            }

            // Show action buttons for articles
            if (type === 'artykuł' && resultData.is_group_lead) {
                actionButtons.classList.remove('hidden');
            }

            // Show meta tags if available
            if (resultData.meta_tags && !resultData.meta_tags.error) {
                document.getElementById('meta-title').value = resultData.meta_tags.meta_title || '';
                document.getElementById('meta-description').value = resultData.meta_tags.meta_description || '';
                metaResult.classList.remove('hidden');
            }

            // Show SEO if available
            if (resultData.seo_article && !resultData.seo_article.error) {
                const seoTitle = resultData.seo_article.seo_title || '';
                const seoMd = resultData.seo_article.seo_article_markdown || '';
                document.getElementById('seo-content').innerHTML = `<h3>${seoTitle}</h3>${seoMd}`;
                document.getElementById('btn-download-seo').href = `/download/seo/${pageNum}`;
                seoResult.classList.remove('hidden');
            }
        } else {
            processedContent.innerHTML = '<p class="placeholder-text">Strona jeszcze nie przetworzona. Kliknij "🚀 Przetwórz".</p>';
        }
    } catch {
        processedContent.innerHTML = '<p class="placeholder-text">Nie można załadować wyniku.</p>';
    }
}

function updateNavigation() {
    const n = state.currentPage;
    document.getElementById('page-indicator').textContent = `${n} / ${state.totalPages}`;
    document.getElementById('btn-prev').disabled = (n <= 1);
    document.getElementById('btn-next').disabled = (n >= state.totalPages);
}

function toggleRawText() {
    document.getElementById('raw-text').classList.toggle('hidden');
}

// ===== PROCESSING =====

async function startProcessing() {
    if (state.processing) return;

    const mode = document.getElementById('process-mode').value;
    const body = { mode };

    if (mode === 'range') {
        body.start_page = parseInt(document.getElementById('range-start').value);
        body.end_page = parseInt(document.getElementById('range-end').value);
    } else if (mode === 'article') {
        body.groups = document.getElementById('article-groups').value;
    }

    state.processing = true;

    // Tryb artykułowy — stary endpoint (nie streamuje)
    if (mode === 'article') {
        showLoading('Przetwarzanie artykułów przez Gemini AI...');
        try {
            const resp = await fetch('/process', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (data.error) {
                showToast(data.error, 'error');
            } else {
                showToast(data.message || 'Przetwarzanie zakończone!', 'success');
                if (data.results && data.results.length > 0) {
                    goToPage(data.results[0].pages[0]);
                }
                loadPage(state.currentPage);
            }
        } catch (err) {
            showToast('Błąd: ' + err.message, 'error');
        } finally {
            state.processing = false;
            hideLoading();
        }
        return;
    }

    // Tryby all / range — SSE streaming z równoległym przetwarzaniem
    showProgress(0, 'Rozpoczynanie przetwarzania...');

    try {
        const resp = await fetch('/process-stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        if (!resp.ok) {
            const errData = await resp.json().catch(() => ({}));
            showToast(errData.error || 'Błąd serwera', 'error');
            state.processing = false;
            hideProgress();
            return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // zachowaj niepełną linię

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const event = JSON.parse(line.slice(6));

                    if (event.error && !event.page_number) {
                        showToast(event.error, 'error');
                        continue;
                    }

                    if (event.done) {
                        showProgress(100, event.message);
                        showToast(event.message, 'success');
                        loadPage(state.currentPage);
                        continue;
                    }

                    // Aktualizacja postępu
                    const pct = event.progress || 0;
                    const statusText = `Strona ${event.page_number}: ${event.type.toUpperCase()} (${event.completed}/${event.total})`;
                    showProgress(pct, statusText);

                } catch (parseErr) {
                    // Pomiń niepoprawne linie
                }
            }
        }
    } catch (err) {
        showToast('Błąd: ' + err.message, 'error');
    } finally {
        state.processing = false;
        setTimeout(hideProgress, 2000);
    }
}

async function rerollPage() {
    // Check if current page is part of a group article
    let groupPages = null;
    try {
        const resultResp = await fetch(`/page/${state.currentPage}/result`);
        const resultData = await resultResp.json();
        if (resultData.processed && resultData.group_pages && resultData.group_pages.length > 1) {
            groupPages = resultData.group_pages;
        }
    } catch { /* ignore */ }

    if (groupPages) {
        // Reprocess entire article group
        const groupStr = groupPages.join(',');
        showLoading(`Ponowne przetwarzanie artykułu (strony ${groupStr})...`);
        try {
            const resp = await fetch('/process', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: 'article', groups: groupStr }),
            });
            const data = await resp.json();
            if (data.error) {
                showToast(data.error, 'error');
            } else {
                showToast('Artykuł przetworzony ponownie!', 'success');
                loadPage(state.currentPage);
            }
        } catch (err) {
            showToast('Błąd: ' + err.message, 'error');
        } finally {
            hideLoading();
        }
    } else {
        // Single page reroll with context
        showLoading(`Ponowne przetwarzanie strony ${state.currentPage}...`);
        try {
            const resp = await fetch(`/process-page/${state.currentPage}`, { method: 'POST' });
            const data = await resp.json();
            if (data.error) {
                showToast(data.error, 'error');
            } else {
                showToast('Strona przetworzona ponownie!', 'success');
                loadPage(state.currentPage);
            }
        } catch (err) {
            showToast('Błąd: ' + err.message, 'error');
        } finally {
            hideLoading();
        }
    }
}

async function generateMeta() {
    showLoading('Generowanie meta tagów...');

    try {
        const resp = await fetch(`/meta/${state.currentPage}`, { method: 'POST' });
        const data = await resp.json();

        if (data.error) {
            showToast(`Błąd: ${data.error}`, 'error');
        } else {
            document.getElementById('meta-title').value = data.meta_title || '';
            document.getElementById('meta-description').value = data.meta_description || '';
            document.getElementById('meta-result').classList.remove('hidden');
            showToast('Meta tagi wygenerowane!', 'success');
        }
    } catch (err) {
        showToast('Błąd: ' + err.message, 'error');
    } finally {
        hideLoading();
    }
}

function generateSeo() {
    // Pre-fill with current page number
    const pagesInput = document.getElementById('seo-pages');
    pagesInput.value = String(state.currentPage);
    document.getElementById('seo-keywords').value = '';
    document.getElementById('seo-modal').classList.remove('hidden');
}

function closeSeoModal() {
    document.getElementById('seo-modal').classList.add('hidden');
}

async function submitSeoGeneration() {
    const pages = document.getElementById('seo-pages').value.trim();
    const keywords = document.getElementById('seo-keywords').value.trim();

    if (!pages) {
        showToast('Podaj numery stron źródłowych.', 'error');
        return;
    }

    closeSeoModal();
    showLoading('Analiza odbiorcy i optymalizacja SEO... To może potrwać dłuższą chwilę.');

    try {
        const resp = await fetch(`/seo/${state.currentPage}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_pages: pages, keywords: keywords }),
        });
        const data = await resp.json();

        if (data.error) {
            showToast(`Błąd: ${data.error}`, 'error');
        } else {
            const seoTitle = data.seo_title || '';
            const seoMd = data.seo_article_markdown || '';
            document.getElementById('seo-content').innerHTML = `<h3>${seoTitle}</h3>${seoMd}`;
            document.getElementById('btn-download-seo').href = `/download/seo/${state.currentPage}`;
            document.getElementById('seo-result').classList.remove('hidden');
            showToast('Wersja SEO gotowa!', 'success');
        }
    } catch (err) {
        showToast('Błąd: ' + err.message, 'error');
    } finally {
        hideLoading();
    }
}

function downloadHtml() {
    window.location.href = `/download/html/${state.currentPage}`;
}

// ===== PROJECT =====

async function saveProject() {
    try {
        const resp = await fetch('/project/save', { method: 'POST' });
        const data = await resp.json();

        if (data.error) {
            showToast(data.error, 'error');
        } else {
            showToast(data.message || 'Projekt zapisany!', 'success');
        }
    } catch (err) {
        showToast('Błąd zapisu: ' + err.message, 'error');
    }
}

// ===== UI HELPERS =====

function showLoading(text) {
    document.getElementById('loading-text').textContent = text || 'Przetwarzanie...';
    document.getElementById('loading-overlay').classList.remove('hidden');
}

function hideLoading() {
    document.getElementById('loading-overlay').classList.add('hidden');
}

function showProgress(percentage, statusText) {
    const progressBar = document.getElementById('progress-bar');
    const progressFill = document.getElementById('progress-fill');
    const progressText = document.getElementById('progress-text');

    progressBar.classList.remove('hidden');
    progressFill.style.width = `${percentage}%`;
    progressText.textContent = statusText || `${percentage}%`;
}

function hideProgress() {
    document.getElementById('progress-bar').classList.add('hidden');
    document.getElementById('progress-fill').style.width = '0%';
    document.getElementById('progress-text').textContent = '0%';
}

function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type}`;
    toast.classList.remove('hidden');

    setTimeout(() => {
        toast.classList.add('hidden');
    }, 4000);
}
