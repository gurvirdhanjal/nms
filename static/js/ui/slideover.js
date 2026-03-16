/**
 * Unified Slide-Over Logic
 * Provides globally accessible methods for side panel components.
 */
(function initializeSlideOver() {
    if (!window.UI) window.UI = {};

    window.UI.SlideOver = {
        _activePanels: new Set(),

        /**
         * Open a slide-over panel
         * @param {string} id - The ID of the slide-over container
         */
        open: function(id) {
            const panel = document.getElementById(id);
            if (!panel) return;
            
            // Toggle visibility
            panel.classList.remove('hidden');
            
            // Allow browser a tick to register removal of hidden before transitioning
            requestAnimationFrame(() => {
                // Background overlay animate in
                const backdrop = panel.querySelector('[data-role="backdrop"]');
                if(backdrop) {
                    backdrop.classList.add('opacity-100');
                    backdrop.classList.remove('opacity-0');
                }
                
                // Panel slide in
                const slider = panel.querySelector('[data-role="slider"]');
                if(slider) {
                    slider.classList.add('translate-x-0');
                    slider.classList.remove('translate-x-full');
                }
            });

            this._activePanels.add(id);
            document.body.style.overflow = 'hidden'; // Prevent background scroll
        },

        /**
         * Close a slide-over panel
         * @param {string} id - The ID of the slide-over container
         */
        close: function(id) {
            const panel = document.getElementById(id);
            if (!panel) return;
            
            // Background overlay animate out
            const backdrop = panel.querySelector('[data-role="backdrop"]');
            if(backdrop) {
                backdrop.classList.remove('opacity-100');
                backdrop.classList.add('opacity-0');
            }
            
            // Panel slide out
            const slider = panel.querySelector('[data-role="slider"]');
            if(slider) {
                slider.classList.remove('translate-x-0');
                slider.classList.add('translate-x-full');
            }
            
            // Wait for transition to complete before hiding container
            setTimeout(() => {
                // Check if it's still closing (user didn't reopen rapidly)
                if(!slider || slider.classList.contains('translate-x-full')) {
                    panel.classList.add('hidden');
                }
            }, 500); // matches the duration-500 class

            this._activePanels.delete(id);
            if (this._activePanels.size === 0) {
                document.body.style.overflow = ''; // Restore background scroll
            }
        },

        /**
         * Toggle a slide-over panel
         * @param {string} id - The ID of the slide-over container
         */
        toggle: function(id) {
            if (this._activePanels.has(id)) {
                this.close(id);
            } else {
                this.open(id);
            }
        },
        
        /**
         * Close all active panels
         */
        closeAll: function() {
            this._activePanels.forEach(id => this.close(id));
        }
    };

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && window.UI.SlideOver._activePanels.size > 0) {
            // Close the most recently opened panel (primitive stack logic)
            const activePanelsArr = Array.from(window.UI.SlideOver._activePanels);
            const topPanelId = activePanelsArr[activePanelsArr.length - 1];
            window.UI.SlideOver.close(topPanelId);
        }
    });
})();
