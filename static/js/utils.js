// Utility functions for formatting
function formatFileSize(bytes) {
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    if (bytes === 0) return '0 Byte';
    const i = parseInt(Math.floor(Math.log(bytes) / Math.log(1024)));
    return Math.round(bytes / Math.pow(1024, i), 2) + ' ' + sizes[i];
}

function formatSpeed(speed) {
    if (typeof speed === 'number') {
        if (speed > 1024 * 1024) {
            return `${(speed / (1024 * 1024)).toFixed(1)} MB/s`;
        } else if (speed > 1024) {
            return `${(speed / 1024).toFixed(1)} KB/s`;
        }
        return `${speed.toFixed(1)} B/s`;
    }
    return speed;
}

function formatETA(eta) {
    if (typeof eta === 'number') {
        const minutes = Math.floor(eta / 60);
        const seconds = Math.floor(eta % 60);
        return `${minutes}:${seconds.toString().padStart(2, '0')} remaining`;
    }
    return eta;
} 