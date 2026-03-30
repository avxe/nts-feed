function showNotification(message, type = 'success') {
    // Remove any existing notifications
    const existingNotification = document.querySelector('.notification');
    if (existingNotification) {
        existingNotification.remove();
    }

    // Create notification element
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.innerHTML = `
        <i class="fas ${type === 'success' ? 'fa-check-circle' : 'fa-exclamation-triangle'}"></i>
        <span>${message}</span>
    `;

    // Add to document
    document.body.appendChild(notification);

    // Show notification with slight delay to ensure transition works
    setTimeout(() => {
        notification.classList.add('show');
    }, 10);

    return new Promise(resolve => {
        // Handle fade out
        setTimeout(() => {
            notification.classList.add('fade-out');
            
            notification.addEventListener('animationend', () => {
                notification.remove();
                resolve();
            }, { once: true });
        }, 2000);
    });
} 

// Persistent notification that stays visible until dismissed.
// Returns a handle with update(message, type) and dismiss(delayMs) methods.
function showPersistentNotification(message, type = 'info') {
    // Remove any existing notifications
    const existingNotification = document.querySelector('.notification');
    if (existingNotification) {
        existingNotification.remove();
    }

    // Create notification element
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.innerHTML = `
        <i class="fas ${type === 'success' ? 'fa-check-circle' : (type === 'error' ? 'fa-exclamation-triangle' : 'fa-circle-info')}"></i>
        <span>${message}</span>
    `;

    // Add to document
    document.body.appendChild(notification);

    // Show notification
    setTimeout(() => {
        notification.classList.add('show');
    }, 10);

    const update = (nextMessage, nextType) => {
        if (typeof nextType === 'string' && nextType) {
            notification.classList.remove('success', 'error', 'info');
            notification.classList.add(nextType);
            const icon = notification.querySelector('i');
            if (icon) {
                icon.classList.remove('fa-check-circle', 'fa-exclamation-triangle', 'fa-circle-info');
                icon.classList.add(nextType === 'success' ? 'fa-check-circle' : (nextType === 'error' ? 'fa-exclamation-triangle' : 'fa-circle-info'));
            }
        }
        if (typeof nextMessage === 'string') {
            const span = notification.querySelector('span');
            if (span) span.textContent = nextMessage;
        }
    };

    const dismiss = (delayMs = 0) => {
        const doDismiss = () => {
            notification.classList.add('fade-out');
            notification.addEventListener('animationend', () => {
                notification.remove();
            }, { once: true });
        };
        if (delayMs > 0) setTimeout(doDismiss, delayMs); else doDismiss();
    };

    return { update, dismiss };
}

// Progress notification with a determinate bar. Returns a handle with:
// update({ percent, text, type }), setType(type), and dismiss(delayMs)
function showProgressNotification(options = {}) {
    const { title = 'Working…', percent = 0, text = '' } = options;

    // Remove any existing notifications
    const existingNotification = document.querySelector('.notification');
    if (existingNotification) {
        existingNotification.remove();
    }

    // Create notification element
    const notification = document.createElement('div');
    notification.className = 'notification info progress';
    notification.innerHTML = `
        <i class="fas fa-sync-alt fa-spin"></i>
        <div class="content">
            <div class="title">${title}</div>
            <div class="subtitle">${text}</div>
            <div class="progress">
                <div class="progress-fill" style="width:${Math.max(0, Math.min(100, percent))}%"></div>
            </div>
        </div>
    `;

    // Add to document
    document.body.appendChild(notification);

    // Show notification
    setTimeout(() => {
        notification.classList.add('show');
    }, 10);

    const setType = (nextType) => {
        if (!nextType) return;
        notification.classList.remove('success', 'error', 'info');
        notification.classList.add(nextType);
        const icon = notification.querySelector('i');
        if (icon) {
            icon.classList.remove('fa-sync-alt', 'fa-spin', 'fa-check-circle', 'fa-exclamation-triangle', 'fa-circle-info');
            if (nextType === 'success') {
                icon.classList.add('fa-check-circle');
            } else if (nextType === 'error') {
                icon.classList.add('fa-exclamation-triangle');
            } else {
                icon.classList.add('fa-circle-info');
            }
        }
    };

    const update = ({ percent: p, text: t, type: tp, title: ttl } = {}) => {
        if (typeof tp === 'string') setType(tp);
        if (typeof ttl === 'string') {
            const el = notification.querySelector('.title');
            if (el) el.textContent = ttl;
        }
        if (typeof t === 'string') {
            const el = notification.querySelector('.subtitle');
            if (el) el.textContent = t;
        }
        if (typeof p === 'number' && !Number.isNaN(p)) {
            const pct = Math.max(0, Math.min(100, Math.round(p)));
            const fill = notification.querySelector('.progress-fill');
            if (fill) fill.style.width = pct + '%';
        }
    };

    const dismiss = (delayMs = 0) => {
        const doDismiss = () => {
            notification.classList.add('fade-out');
            notification.addEventListener('animationend', () => {
                notification.remove();
            }, { once: true });
        };
        if (delayMs > 0) setTimeout(doDismiss, delayMs); else doDismiss();
    };

    return { update, setType, dismiss };
}