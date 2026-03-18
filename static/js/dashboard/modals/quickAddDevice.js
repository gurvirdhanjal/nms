/**
 * Modal Controller: Quick Add Device
 * Extracted from dashboard.html inline script block.
 */

export function initQuickAddDevice() {
    const modal = document.getElementById('dash-add-device-modal');
    const form = document.getElementById('dash-add-device-form');
    const open = document.getElementById('dash-add-device-btn');
    const close = document.getElementById('dash-modal-close');
    const cancel = document.getElementById('dash-modal-cancel');
    const submit = form ? form.querySelector('button[type="submit"]') : null;
    const nameInput = document.getElementById('dash-device-name');

    if (!modal || !form || !open || !close || !cancel || !submit) {
        return;
    }

    let submitting = false;
    const defaultSubmitLabel = submit.textContent.trim();

    function setSubmitting(isBusy) {
        submitting = isBusy;
        submit.disabled = isBusy;
        submit.setAttribute('aria-busy', isBusy ? 'true' : 'false');
        submit.textContent = isBusy ? 'Saving...' : defaultSubmitLabel;
    }

    // Lazy-load compliance profiles into the dropdown (once per page load)
    let profilesLoaded = false;
    function loadComplianceProfiles() {
        if (profilesLoaded) { return; }
        const sel = document.getElementById('dash-compliance-profile');
        if (!sel) { return; }
        fetch('/api/compliance-profiles', { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : []; })
            .then(function (profiles) {
                profiles.forEach(function (p) {
                    const opt = document.createElement('option');
                    opt.value = p.id;
                    opt.textContent = p.name;
                    sel.appendChild(opt);
                });
                profilesLoaded = true;
            })
            .catch(function () {});
    }

    function show() {
        loadComplianceProfiles();
        modal.style.display = 'flex';
        requestAnimationFrame(() => nameInput?.focus());
    }

    function hide() {
        if (submitting) {
            return;
        }
        modal.style.display = 'none';
        form.reset();
        setSubmitting(false);
    }

    open.addEventListener('click', show);
    close.addEventListener('click', hide);
    cancel.addEventListener('click', hide);
    modal.addEventListener('click', function (e) { if (e.target === modal) hide(); });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && modal.style.display !== 'none') {
            hide();
        }
    });

    form.addEventListener('submit', function (e) {
        if (submitting) {
            e.preventDefault();
            return;
        }
        setSubmitting(true);

        // Preserve a usable UI if navigation is interrupted by the browser or network.
        window.setTimeout(function () {
            if (document.body.contains(form)) {
                setSubmitting(false);
            }
        }, 10000);
    });
}
