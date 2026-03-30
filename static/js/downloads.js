class DownloadManager {
    static initMessages = [
        'Searching for audio file...',
        'Fetching episode metadata...',
        'Starting download...'
    ];

    static async handleSingleDownload(button, url) {
        if (button.classList.contains('loading')) return;
        
        try {
            button.classList.add('loading');
            button.querySelector('.button-text').textContent = 'Downloading...';
            
            const episodeItem = button.closest('.episode-item');
            const progressDiv = episodeItem.querySelector('.download-progress');
            const progressBar = progressDiv.querySelector('.progress-bar');
            const progressInfo = progressDiv.querySelector('.progress-info');
            
            progressDiv.style.display = 'block';
            progressBar.style.width = '0%';
            
            // Cycle through init messages
            let messageIndex = 0;
            progressInfo.textContent = this.initMessages[0];
            const messageInterval = setInterval(() => {
                messageIndex = (messageIndex + 1) % this.initMessages.length;
                progressInfo.textContent = this.initMessages[messageIndex];
            }, 2000);

            const response = await fetch(url);
            if (!response.ok) throw new Error('Failed to start download');
            
            clearInterval(messageInterval);
            
            const data = await response.json();
            await this.setupEventSource(data.download_id, button, progressDiv);
            
        } catch (error) {
            console.error('Download error:', error);
            button.classList.remove('loading');
            button.classList.add('error');
            button.querySelector('.button-text').textContent = 'Error';
            alert(`Download error: ${error.message}`);
        }
    }

    static async setupEventSource(downloadId, button, progressDiv) {
        const progressBar = progressDiv.querySelector('.progress-bar');
        const progressInfo = progressDiv.querySelector('.progress-info');
        const eventSource = new EventSource(`/progress/${downloadId}`);
        
        progressDiv.dataset.eventSource = `/progress/${downloadId}`;
        
        eventSource.onmessage = function(event) {
            const progress = JSON.parse(event.data);
            
            if (progress === null) {
                eventSource.close();
                button.classList.remove('loading');
                button.querySelector('.button-text').textContent = 'Download mix';
                progressDiv.style.display = 'none';
                return;
            }
            
            if (progress.status === 'progress') {
                if (progress.percent >= 95) {
                    progressBar.style.display = 'none';
                    progressInfo.textContent = 'Converting file to AAC (.m4a)...';
                } else {
                    progressBar.style.setProperty('--progress-width', `${progress.percent}%`);
                    let statusText = `${progress.percent.toFixed(1)}%`;
                    if (progress.speed) statusText += ` - ${formatSpeed(progress.speed)}`;
                    if (progress.eta) statusText += ` - ${formatETA(progress.eta)}`;
                    progressInfo.textContent = statusText;
                }
            } else if (progress.status === 'error') {
                eventSource.close();
                button.classList.remove('loading');
                button.classList.add('error');
                button.querySelector('.button-text').textContent = 'Error';
                progressInfo.textContent = progress.message;
                progressInfo.style.color = 'var(--color-error)';
            }
        };
        
        eventSource.onerror = function() {
            eventSource.close();
            button.classList.remove('loading');
            button.querySelector('.button-text').textContent = 'Download';
            progressDiv.style.display = 'none';
        };
    }
} 