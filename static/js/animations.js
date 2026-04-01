/**
 * animations.js — Performance-First Animation System
 * GPU-accelerated, 60fps, Intersection Observer-based
 *
 * IMPORTANT: body has overflow:hidden in this app.
 * All scroll listeners target .app-content, not window.
 */

'use strict';

/* ── Scroll target adapter ──────────────────────────────────── */
function _getScroller() {
    return document.querySelector('.app-content') || window;
}

/* ── 1. SmoothScrollController ──────────────────────────────── */
class SmoothScrollController {
    constructor() {
        this._scroller = _getScroller();
        this._bindAnchorLinks();
    }

    _bindAnchorLinks() {
        document.querySelectorAll('a[href^="#"]').forEach(a => {
            a.addEventListener('click', this._handleClick.bind(this));
        });
    }

    _handleClick(e) {
        const href = e.currentTarget.getAttribute('href');
        if (!href || href === '#') return;
        const target = document.querySelector(href);
        if (!target) return;
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

/* ── 2. FadeInSection ───────────────────────────────────────── */
class FadeInSection {
    constructor(selector = '[data-fade-in]', options = {}) {
        this._io = new IntersectionObserver(this._onIntersect.bind(this), {
            threshold: options.threshold ?? 0.12,
            rootMargin: options.rootMargin ?? '0px 0px -32px 0px',
            root: options.root ?? null,
        });

        document.querySelectorAll(selector).forEach(el => {
            el.classList.add('fade-in-ready');
            this._io.observe(el);
        });
    }

    _onIntersect(entries) {
        entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            entry.target.classList.add('fade-in-visible');
            this._io.unobserve(entry.target);
        });
    }
}

/* ── 3. StaggerAnimation ────────────────────────────────────── */
class StaggerAnimation {
    constructor(containerSelector = '[data-stagger]', options = {}) {
        this._delay = options.delay ?? 80;
        this._io = new IntersectionObserver(this._onIntersect.bind(this), {
            threshold: 0.08,
            rootMargin: '0px 0px -24px 0px',
        });

        document.querySelectorAll(containerSelector).forEach(container => {
            container.querySelectorAll('[data-stagger-item]').forEach(item => {
                item.classList.add('stagger-item');
            });
            this._io.observe(container);
        });
    }

    _onIntersect(entries) {
        entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            const items = entry.target.querySelectorAll('.stagger-item');
            items.forEach((item, i) => {
                item.style.transitionDelay = `${i * this._delay}ms`;
                // Use RAF to ensure CSS transition triggers after display
                requestAnimationFrame(() => item.classList.add('fade-in-visible'));
            });
            this._io.unobserve(entry.target);
        });
    }
}

/* ── 4. MagneticButton ──────────────────────────────────────── */
class MagneticButton {
    constructor(selector = '[data-magnetic], .btn-primary') {
        document.querySelectorAll(selector).forEach(btn => {
            btn.addEventListener('mousemove', this._onMove.bind(this));
            btn.addEventListener('mouseleave', this._onLeave.bind(this));
        });
    }

    _onMove(e) {
        const btn = e.currentTarget;
        const rect = btn.getBoundingClientRect();
        const x = e.clientX - rect.left - rect.width * 0.5;
        const y = e.clientY - rect.top - rect.height * 0.5;
        const strength = 0.28;
        btn.style.transform = `translate(${(x * strength).toFixed(2)}px, ${(y * strength).toFixed(2)}px)`;
    }

    _onLeave(e) {
        e.currentTarget.style.transform = '';
    }
}

/* ── 5. GlassCard ───────────────────────────────────────────── */
class GlassCard {
    constructor(selector = '[data-glass]') {
        document.querySelectorAll(selector).forEach(card => {
            if (!card.classList.contains('glass-card')) {
                card.classList.add('glass-card');
            }
        });
    }
}

/* ── 6. HoverRevealCard ─────────────────────────────────────── */
class HoverRevealCard {
    constructor(selector = '[data-hover-reveal]') {
        document.querySelectorAll(selector).forEach(card => {
            card.classList.add('hover-reveal-card');

            // Auto-create overlay if it doesn't exist
            if (!card.querySelector('.hover-overlay')) {
                const overlay = document.createElement('div');
                overlay.className = 'hover-overlay';
                const label = card.dataset.hoverLabel;
                if (label) {
                    overlay.textContent = label;
                    overlay.style.cssText = 'color:#fff;font-family:Rajdhani,sans-serif;font-size:0.85rem;font-weight:600;letter-spacing:0.05em;';
                }
                card.appendChild(overlay);
            }
        });
    }
}

/* ── 7. TextReveal ──────────────────────────────────────────── */
class TextReveal {
    constructor(selector = '[data-text-reveal]') {
        document.querySelectorAll(selector).forEach(el => {
            const mode = el.dataset.textReveal || 'word';
            this._split(el, mode);
        });
    }

    _split(el, mode) {
        const text = el.textContent.trim();
        if (!text) return;

        el.textContent = '';
        el.classList.add('text-reveal-container');

        const units = mode === 'letter' ? [...text] : text.split(' ');

        units.forEach((unit, i) => {
            const span = document.createElement('span');
            span.className = 'text-reveal-unit';
            span.textContent = mode === 'word' ? unit + '\u00A0' : unit;
            span.style.transitionDelay = `${i * 38}ms`;
            el.appendChild(span);
        });

        // Trigger reveal after two animation frames to ensure paint
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                el.classList.add('text-reveal-active');
            });
        });
    }
}

/* ── 8. ParallaxEffect ──────────────────────────────────────── */
class ParallaxEffect {
    constructor(selector = '[data-parallax]') {
        this._elements = [...document.querySelectorAll(selector)];
        if (!this._elements.length) return;

        this._scroller = _getScroller();
        this._rafPending = false;

        this._scroller.addEventListener('scroll', this._onScroll.bind(this), { passive: true });
        this._update();
    }

    _onScroll() {
        if (this._rafPending) return;
        this._rafPending = true;
        requestAnimationFrame(() => {
            this._update();
            this._rafPending = false;
        });
    }

    _update() {
        const scrollTop = this._scroller === window
            ? window.pageYOffset
            : this._scroller.scrollTop;

        this._elements.forEach(el => {
            const speed = parseFloat(el.dataset.parallax) || 0.25;
            el.style.transform = `translateY(${(scrollTop * speed).toFixed(2)}px)`;
        });
    }
}

/* ── 9. StickySection ───────────────────────────────────────── */
class StickySection {
    constructor(selector = '[data-sticky-section]') {
        // CSS handles position:sticky — JS adds IntersectionObserver
        // to track when section is actually stuck
        const sentinel = (section) => {
            const s = document.createElement('div');
            s.style.cssText = 'position:absolute;top:-1px;left:0;width:1px;height:1px;pointer-events:none;';
            section.style.position = 'relative';
            section.insertBefore(s, section.firstChild);
            return s;
        };

        const io = new IntersectionObserver(
            entries => entries.forEach(e => {
                const section = e.target.parentElement;
                if (section) section.classList.toggle('is-stuck', !e.isIntersecting);
            }),
            { threshold: [0], rootMargin: '-1px 0px 0px 0px' }
        );

        document.querySelectorAll(selector).forEach(section => {
            io.observe(sentinel(section));
        });
    }
}

/* ── 10. NavbarController ───────────────────────────────────── */
class NavbarController {
    constructor(selector = '.app-header') {
        this._header = document.querySelector(selector);
        if (!this._header) return;

        this._scroller = _getScroller();
        this._rafPending = false;
        this._lastScrolled = false;

        this._scroller.addEventListener('scroll', this._onScroll.bind(this), { passive: true });
    }

    _onScroll() {
        if (this._rafPending) return;
        this._rafPending = true;
        requestAnimationFrame(() => {
            const scrollTop = this._scroller === window
                ? window.pageYOffset
                : this._scroller.scrollTop;

            const shouldScroll = scrollTop > 40;
            if (shouldScroll !== this._lastScrolled) {
                this._header.classList.toggle('header-scrolled', shouldScroll);
                this._lastScrolled = shouldScroll;
            }
            this._rafPending = false;
        });
    }
}

/* ── Auto-Initialization ────────────────────────────────────── */
/* Only NavbarController auto-inits. All other classes are opt-in via window.Animations. */
document.addEventListener('DOMContentLoaded', () => {
    new NavbarController();
});

/* Opt-in registry — use on pages that need specific animation classes */
window.Animations = {
    SmoothScrollController,
    FadeInSection,
    StaggerAnimation,
    MagneticButton,
    GlassCard,
    HoverRevealCard,
    TextReveal,
    ParallaxEffect,
    StickySection,
};
