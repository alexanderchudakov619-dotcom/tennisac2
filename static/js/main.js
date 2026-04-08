// ── Dropzone drag & drop behavior ────────────────────
const dropzone = document.getElementById('dropzone');
const videoInput = document.getElementById('videoInput');
const dzInner = document.getElementById('dzInner');

if (dropzone) {
  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  });

  dropzone.addEventListener('dragleave', () => {
    dropzone.classList.remove('dragover');
  });

  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file && videoInput) {
      // Set file on input
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(file);
      videoInput.files = dataTransfer.files;
      updateDropzoneLabel(file.name);
    }
  });
}

if (videoInput) {
  videoInput.addEventListener('change', () => {
    const file = videoInput.files[0];
    if (file) updateDropzoneLabel(file.name);
  });
}

function updateDropzoneLabel(filename) {
  if (!dzInner) return;
  dzInner.innerHTML = `
    <div class="dz-icon">✅</div>
    <p class="dz-text">${filename}</p>
    <p class="dz-sub">Ready to analyze · click to change</p>
  `;
}

// ── Form submit loader ────────────────────────────────
const uploadForm = document.getElementById('uploadForm');
const btnText = document.getElementById('btnText');
const btnLoader = document.getElementById('btnLoader');

if (uploadForm) {
  uploadForm.addEventListener('submit', (e) => {
    if (videoInput && videoInput.files.length === 0) {
      e.preventDefault();
      alert('Please select a video file first.');
      return;
    }
    if (btnText && btnLoader) {
      btnText.classList.add('hidden');
      btnLoader.classList.remove('hidden');
    }
  });
}
