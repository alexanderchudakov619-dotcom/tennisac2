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

// Same limit as the server's MAX_CONTENT_LENGTH.
const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

function updateDropzoneLabel(filename) {
  if (!dzInner) return;
  const file = videoInput && videoInput.files[0];
  const mb = file ? file.size / 1e6 : 0;
  // Big phone clips (4K) take a long time to upload on cell data.
  const note = file && file.size > MAX_UPLOAD_BYTES
    ? `Too big (${Math.round(mb)} MB, max 100). Trim it to just the swing, or film in 1080p.`
    : mb > 40
      ? `${Math.round(mb)} MB · big file, the upload may take a minute. Trimming to just the swing makes it faster.`
      : 'Ready to analyze · click to change';
  dzInner.innerHTML = '';
  const icon = document.createElement('div'); icon.className = 'dz-icon'; icon.textContent = '✅';
  const name = document.createElement('p'); name.className = 'dz-text'; name.textContent = filename;
  const sub = document.createElement('p'); sub.className = 'dz-sub'; sub.textContent = note;
  dzInner.append(icon, name, sub);
}

// ── Upload with progress ─────────────────────────────
// Sends the video in the background so the page can show how the upload
// is going and what the analysis is doing, instead of a frozen button.
const uploadForm = document.getElementById('uploadForm');
const btnText = document.getElementById('btnText');
const btnLoader = document.getElementById('btnLoader');
const submitBtn = document.getElementById('submitBtn');
const status = document.getElementById('analyseStatus');
const stepText = document.getElementById('asStep');
const subText = document.getElementById('asSub');
const fill = document.getElementById('asFill');

const ANALYSIS_STEPS = [
  'Finding you in the video…',
  'Tracking your body, frame by frame…',
  'Finding the moment you hit the ball…',
  'Measuring each phase of the stroke…',
  'Scoring and writing your notes…',
];

function resetUpload(message) {
  status.hidden = true;
  submitBtn.style.display = '';
  submitBtn.disabled = false;
  btnText.classList.remove('hidden');
  btnLoader.classList.add('hidden');
  if (message) alert(message);
}

if (uploadForm) {
  uploadForm.addEventListener('submit', (e) => {
    if (videoInput && videoInput.files.length === 0) {
      e.preventDefault();
      alert('Please select a video file first.');
      return;
    }
    if (videoInput.files[0].size > MAX_UPLOAD_BYTES) {
      e.preventDefault();
      alert('That video is over 100 MB. Trim it to just the swing (or film in 1080p) and try again.');
      return;
    }
    if (btnText && btnLoader) {
      btnText.classList.add('hidden');
      btnLoader.classList.remove('hidden');
    }
    if (!status || !window.FormData || !window.XMLHttpRequest) return;  // plain form post

    e.preventDefault();
    submitBtn.disabled = true;
    submitBtn.style.display = 'none';
    status.hidden = false;
    stepText.textContent = 'Uploading your video…';
    subText.textContent = 'Keep this page open.';
    fill.style.width = '0%';

    let timer = null;
    const xhr = new XMLHttpRequest();
    xhr.open('POST', uploadForm.action);
    xhr.upload.addEventListener('progress', (ev) => {
      if (!ev.lengthComputable) return;
      const pct = Math.round(ev.loaded / ev.total * 100);
      stepText.textContent = `Uploading your video… ${pct}%`;
      fill.style.width = (pct * 0.3) + '%';  // the upload is the first 30% of the bar
    });
    xhr.upload.addEventListener('load', () => {
      // The server doesn't report its progress, so walk through the steps
      // and ease the bar toward (never reaching) the end.
      let step = 0, done = 30;
      stepText.textContent = ANALYSIS_STEPS[0];
      subText.textContent = 'Usually under a minute. Keep this page open.';
      timer = setInterval(() => {
        done += (97 - done) * 0.06;
        fill.style.width = done + '%';
        const next = Math.min(ANALYSIS_STEPS.length - 1, Math.floor((done - 30) / 13));
        if (next !== step) { step = next; stepText.textContent = ANALYSIS_STEPS[step]; }
      }, 1000);
    });
    xhr.addEventListener('load', () => {
      clearInterval(timer);
      fill.style.width = '100%';
      // Errors redirect back here with a message: follow the redirect.
      const landed = xhr.responseURL ? new URL(xhr.responseURL) : null;
      if (landed && landed.pathname !== new URL(uploadForm.action, location.href).pathname) {
        location.href = xhr.responseURL;
        return;
      }
      document.open();
      document.write(xhr.responseText);
      document.close();
      window.scrollTo(0, 0);
    });
    xhr.addEventListener('error', () => {
      clearInterval(timer);
      resetUpload('The upload stopped. Check your connection and try again.');
    });
    xhr.send(new FormData(uploadForm));
  });
}
