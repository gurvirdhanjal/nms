// file name: static/js/session_manager.js (updated)
class SessionManager {
    constructor() {
        this.checkInterval = 30000; // Check every 30 seconds
        this.warningTime = 60000; // Warn 1 minute before logout
        this.timeout = 300000; // 5 minutes in milliseconds
        this.activityPingMinInterval = 60000; // At most 1 activity ping per minute
        this.warningShown = false;
        this.isChecking = false;
        this.lastActivityPingAt = 0;
        this.activityPingInFlight = false;
        
        // Check if we're on login page
        if (window.location.pathname === '/login' || 
            window.location.pathname === '/register' ||
            window.location.pathname === '/forgot_password') {
            return; // Don't run on auth pages
        }
        
        this.init();
    }
    
    init() {
        // Initial check
        setTimeout(() => this.checkSession(), 1000);
        
        // Set up periodic checking
        this.checkIntervalId = setInterval(() => this.checkSession(), this.checkInterval);
        
        // Reset timer on user activity
        this.setupActivityListeners();
    }
    
    setupActivityListeners() {
        // Keep this lightweight: only meaningful user intent events.
        const activityEvents = ['click', 'keydown', 'touchstart'];
        
        activityEvents.forEach(event => {
            document.addEventListener(event, () => {
                if (!this.warningShown) {
                    this.sendActivityPing();
                }
            }, { passive: true });
        });
    }
    
    async checkSession() {
        // Prevent multiple simultaneous checks
        if (this.isChecking) return;
        
        this.isChecking = true;
        
        try {
            const response = await fetch('/session-status');
            const data = await response.json();
            
            // If not logged in at all, don't do anything
            if (!data.logged_in) {
                this.isChecking = false;
                return;
            }
            
            // If session is invalid, logout
            if (!data.valid_session) {
                this.logout();
                return;
            }
            
            // Calculate remaining time
            const remainingMs = data.remaining_time * 1000;
            
            // Show warning if less than warningTime remains
            if (remainingMs <= this.warningTime && !this.warningShown) {
                this.showWarning(Math.floor(remainingMs / 1000));
            }
            
        } catch (error) {
            console.error('Session check failed:', error);
        } finally {
            this.isChecking = false;
        }
    }
    
    async sendActivityPing() {
        const now = Date.now();
        if (this.activityPingInFlight) return;
        if (now - this.lastActivityPingAt < this.activityPingMinInterval) return;

        this.activityPingInFlight = true;
        this.lastActivityPingAt = now;
        try {
            await fetch('/session-status', { credentials: 'same-origin' });
        } catch (error) {
            // Silent fail
        } finally {
            this.activityPingInFlight = false;
        }
    }
    
    showWarning(secondsRemaining) {
        this.warningShown = true;
        
        // Remove existing warning if any
        this.removeWarning();
        
        // Create warning modal
        const warningDiv = document.createElement('div');
        warningDiv.className = 'session-warning-modal';
        warningDiv.id = 'sessionWarning';
        warningDiv.innerHTML = `
            <div class="session-warning-content">
                <div class="session-warning-header">
                    <i class="fas fa-exclamation-triangle"></i>
                    <h4>Session Expiring Soon</h4>
                </div>
                <div class="session-warning-body">
                    <p>Your session will expire in <span class="countdown">${secondsRemaining}</span> seconds due to inactivity.</p>
                    <p>Click anywhere or press any key to stay logged in.</p>
                </div>
                <div class="session-warning-footer">
                    <button class="btn btn-primary btn-sm" onclick="sessionManager.extendSession()">
                        <i class="fas fa-sync-alt"></i> Stay Logged In
                    </button>
                </div>
            </div>
        `;
        
        document.body.appendChild(warningDiv);

        const dismissOnActivity = () => {
            if (!this.warningShown) return;
            this.extendSession();
            document.removeEventListener('click', dismissOnActivity, true);
            document.removeEventListener('keypress', dismissOnActivity, true);
        };
        document.addEventListener('click', dismissOnActivity, true);
        document.addEventListener('keypress', dismissOnActivity, true);
        
        // Start countdown
        this.startCountdown(secondsRemaining);
    }
    
    startCountdown(seconds) {
        const countdownElement = document.querySelector('#sessionWarning .countdown');
        if (!countdownElement) return;
        
        let remaining = seconds;
        const interval = setInterval(() => {
            remaining--;
            countdownElement.textContent = remaining;
            
            if (remaining <= 0 || !this.warningShown) {
                clearInterval(interval);
                if (remaining <= 0) {
                    this.logout();
                }
            }
        }, 1000);
    }
    
    removeWarning() {
        const warning = document.getElementById('sessionWarning');
        if (warning) {
            warning.remove();
        }
    }
    
    async extendSession() {
        try {
            // Make a request to update session activity
            await fetch('/session-status', { credentials: 'same-origin' });
            this.lastActivityPingAt = Date.now();
            this.warningShown = false;
            this.removeWarning();
        } catch (error) {
            console.error('Failed to extend session:', error);
        }
    }
    
    logout() {
        // Clear interval
        if (this.checkIntervalId) {
            clearInterval(this.checkIntervalId);
        }
        
        // Redirect to logout
        window.location.href = '/logout';
    }
}

// Initialize only if user is logged in
document.addEventListener('DOMContentLoaded', () => {
    // Check if user is logged in by looking for logout link or session data
    const logoutLink = document.querySelector('a[href*="logout"]');
    const userDropdown = document.querySelector('.navbar-nav .dropdown-toggle');
    
    if (logoutLink || userDropdown) {
        window.sessionManager = new SessionManager();
    }
});
