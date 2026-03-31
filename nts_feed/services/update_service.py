"""
Update Service for NTS Feed

Handles concurrent show updates with progress reporting and better error handling.
Provides significant performance improvements over sequential processing.
"""

import logging
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import queue

from ..scrape import (
    load_shows, save_shows, load_episodes, save_episodes,
    slugify, check_new_episodes
)
from ..downloader import download, download_manager
from ..ext.tasks import get_executor
from ..runtime_paths import downloads_dir

logger = logging.getLogger(__name__)

# Downloads directory (storage/downloads)
DOWNLOAD_DIR = downloads_dir()
DOWNLOAD_DIR.mkdir(exist_ok=True)


@dataclass
class UpdateProgress:
    """Data class for tracking update progress"""
    update_id: str
    total_shows: int
    completed_shows: int
    current_show: Optional[str]
    status: str  # 'running', 'completed', 'failed', 'cancelled'
    start_time: float
    end_time: Optional[float]
    total_new_episodes: int
    total_auto_downloaded: int
    errors: List[Dict]
    show_results: List[Dict]
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)
    
    @property
    def progress_percentage(self) -> float:
        """Calculate completion percentage"""
        if self.total_shows == 0:
            return 100.0
        return (self.completed_shows / self.total_shows) * 100
    
    @property
    def elapsed_time(self) -> float:
        """Calculate elapsed time in seconds"""
        end = self.end_time or time.time()
        return end - self.start_time
    
    @property
    def estimated_time_remaining(self) -> Optional[float]:
        """Estimate remaining time based on current progress"""
        if self.completed_shows == 0 or self.status != 'running':
            return None
        
        avg_time_per_show = self.elapsed_time / self.completed_shows
        remaining_shows = self.total_shows - self.completed_shows
        return avg_time_per_show * remaining_shows


@dataclass
class ShowUpdateResult:
    """Result of updating a single show"""
    show_url: str
    show_title: str
    success: bool
    new_episodes_count: int
    auto_downloaded_count: int
    error_message: Optional[str]
    processing_time: float
    show_slug: str
    new_episodes: List[Dict]


class UpdateService:
    """
    Service for managing concurrent show updates with progress tracking.
    
    Features:
    - Concurrent processing of multiple shows
    - Real-time progress tracking
    - Error handling and recovery
    - Background task management
    """
    
    def __init__(self, max_workers: int = 5):
        """
        Initialize the UpdateService
        
        Args:
            max_workers: Maximum number of concurrent show updates (default: 5)
        """
        self.max_workers = max_workers
        self.active_updates: Dict[str, UpdateProgress] = {}
        self.progress_queues: Dict[str, queue.Queue] = {}
        self._lock = threading.Lock()
        
        logger.debug("UpdateService initialized with %s max workers", max_workers)
    
    def start_update(self, enable_auto_download: bool = True) -> str:
        """
        Start a background update of all shows
        
        Args:
            enable_auto_download: Whether to process auto-downloads after update
            
        Returns:
            update_id: Unique identifier for tracking this update
        """
        update_id = str(uuid.uuid4())
        
        # Initialize progress tracking
        shows = load_shows()
        progress = UpdateProgress(
            update_id=update_id,
            total_shows=len(shows),
            completed_shows=0,
            current_show=None,
            status='running',
            start_time=time.time(),
            end_time=None,
            total_new_episodes=0,
            total_auto_downloaded=0,
            errors=[],
            show_results=[]
        )
        
        with self._lock:
            self.active_updates[update_id] = progress
            self.progress_queues[update_id] = queue.Queue()
        
        # Start background update on the shared executor
        get_executor().submit(self._update_all_shows_background, update_id, enable_auto_download)
        
        logger.info(f"Started background update {update_id} for {len(shows)} shows")
        return update_id
    
    def get_progress(self, update_id: str) -> Optional[UpdateProgress]:
        """Get current progress for an update"""
        with self._lock:
            return self.active_updates.get(update_id)
    
    def get_progress_queue(self, update_id: str) -> Optional[queue.Queue]:
        """Get progress queue for SSE streaming"""
        with self._lock:
            return self.progress_queues.get(update_id)
    
    def cancel_update(self, update_id: str) -> bool:
        """Cancel a running update"""
        with self._lock:
            if update_id in self.active_updates:
                progress = self.active_updates[update_id]
                if progress.status == 'running':
                    progress.status = 'cancelled'
                    progress.end_time = time.time()
                    
                    # Notify progress queue
                    if update_id in self.progress_queues:
                        self.progress_queues[update_id].put({
                            'type': 'status_change',
                            'status': 'cancelled',
                            'message': 'Update cancelled by user'
                        })
                    
                    logger.info(f"Cancelled update {update_id}")
                    return True
        return False
    
    def cleanup_completed_updates(self, max_age_hours: int = 24):
        """Clean up old completed updates to prevent memory leaks"""
        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        
        with self._lock:
            to_remove = []
            for update_id, progress in self.active_updates.items():
                if (progress.status in ['completed', 'failed', 'cancelled'] and 
                    progress.end_time and 
                    current_time - progress.end_time > max_age_seconds):
                    to_remove.append(update_id)
            
            for update_id in to_remove:
                self.active_updates.pop(update_id, None)
                self.progress_queues.pop(update_id, None)
                logger.debug(f"Cleaned up old update {update_id}")
    
    def _update_all_shows_background(self, update_id: str, enable_auto_download: bool):
        """Background task for updating all shows concurrently"""
        try:
            shows = load_shows()
            progress = self.active_updates[update_id]
            progress_queue = self.progress_queues[update_id]
            
            # Send initial progress
            progress_queue.put({
                'type': 'started',
                'total_shows': len(shows),
                'message': f'Starting update of {len(shows)} shows...'
            })
            
            # Process shows concurrently
            show_items = list(shows.items())
            show_results = []
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all show update tasks
                future_to_show = {
                    executor.submit(self._update_single_show, url, show_data, enable_auto_download): (url, show_data)
                    for url, show_data in show_items
                }
                
                # Process completed tasks
                for future in as_completed(future_to_show):
                    if progress.status == 'cancelled':
                        break
                    
                    url, show_data = future_to_show[future]
                    
                    try:
                        result = future.result()
                        show_results.append(result)
                        
                        # Update progress
                        with self._lock:
                            progress.completed_shows += 1
                            progress.current_show = result.show_title
                            progress.total_new_episodes += result.new_episodes_count
                            progress.total_auto_downloaded += result.auto_downloaded_count
                            
                            if not result.success:
                                progress.errors.append({
                                    'show_url': result.show_url,
                                    'show_title': result.show_title,
                                    'error': result.error_message
                                })
                        
                        # Send progress update with detailed episode information
                        progress_queue.put({
                            'type': 'progress',
                            'completed_shows': progress.completed_shows,
                            'total_shows': progress.total_shows,
                            'current_show': result.show_title,
                            'progress_percentage': progress.progress_percentage,
                            'new_episodes_found': result.new_episodes_count,
                            'total_new_episodes': progress.total_new_episodes,
                            'success': result.success,
                            'estimated_time_remaining': progress.estimated_time_remaining,
                            'processing_time': result.processing_time
                        })
                        
                    except Exception as e:
                        logger.error(f"Error processing show {url}: {e}")
                        with self._lock:
                            progress.completed_shows += 1
                            progress.errors.append({
                                'show_url': url,
                                'show_title': show_data.get('title', 'Unknown'),
                                'error': str(e)
                            })
            
            # After all workers finish, apply aggregated writes:
            show_updates: Dict[str, Dict] = {}
            for result in show_results:
                if result.success and result.new_episodes_count > 0:
                    try:
                        # Sequentially update episodes file for this show
                        episodes_data = load_episodes(result.show_slug) or {"episodes": []}
                        existing_episodes = episodes_data.get("episodes", [])
                        
                        # Deduplicate: only add new episodes that don't already exist by URL
                        existing_urls = {
                            ep.get("url") or ep.get("audio_url") 
                            for ep in existing_episodes 
                            if ep.get("url") or ep.get("audio_url")
                        }
                        new_unique = [
                            ep for ep in result.new_episodes 
                            if (ep.get("url") or ep.get("audio_url")) not in existing_urls
                        ]
                        merged = new_unique + list(existing_episodes)
                        save_episodes(result.show_slug, {"episodes": merged})

                        # Collect show metadata updates to apply in a single write later
                        show_updates[result.show_url] = {
                            'total_episodes': len(merged),
                            'new_episodes': len(new_unique),
                            'last_updated': datetime.now().isoformat(),
                        }
                    except Exception as e:
                        logger.error(f"Failed to persist updates for {result.show_url}: {e}")

            if show_updates:
                try:
                    shows_doc = load_shows()
                    for url, updates in show_updates.items():
                        if url in shows_doc:
                            shows_doc[url].update(updates)
                    save_shows(shows_doc)
                except Exception as e:
                    logger.error(f"Failed to persist aggregated show metadata: {e}")

            # Mark as completed
            with self._lock:
                if progress.status != 'cancelled':
                    progress.status = 'completed'
                progress.end_time = time.time()
                progress.show_results = [asdict(result) for result in show_results]
            
            # Send completion notification
            progress_queue.put({
                'type': 'completed',
                'status': progress.status,
                'total_new_episodes': progress.total_new_episodes,
                'total_auto_downloaded': progress.total_auto_downloaded,
                'errors_count': len(progress.errors),
                'elapsed_time': progress.elapsed_time,
                'message': f'Update completed! Found {progress.total_new_episodes} new episodes.'
            })
            
            logger.info(f"Update {update_id} completed: {progress.total_new_episodes} new episodes, "
                       f"{len(progress.errors)} errors, {progress.elapsed_time:.1f}s")
            
        except Exception as e:
            logger.error(f"Critical error in background update {update_id}: {e}")
            with self._lock:
                progress.status = 'failed'
                progress.end_time = time.time()
                progress.errors.append({
                    'show_url': 'SYSTEM',
                    'show_title': 'System Error',
                    'error': str(e)
                })
            
            progress_queue.put({
                'type': 'error',
                'message': f'Update failed: {str(e)}'
            })
        
        finally:
            # Signal end of progress stream
            progress_queue.put(None)
    
    def _update_single_show(self, show_url: str, show_data: Dict, enable_auto_download: bool) -> ShowUpdateResult:
        """
        Update a single show and return the result
        
        Args:
            show_url: URL of the show to update
            show_data: Current show metadata
            
        Returns:
            ShowUpdateResult with the update outcome
        """
        start_time = time.time()
        show_title = show_data.get('title', 'Unknown Show')
        auto_downloaded_episodes = 0
        
        try:
            logger.debug(f"Updating show: {show_title}")
            
            # Get current episodes
            show_slug = slugify(show_url)
            episodes_data = load_episodes(show_slug)

            # Check for new episodes
            new_episodes = check_new_episodes(show_url, episodes_data['episodes'])

            if new_episodes:
                logger.info(f"Found {len(new_episodes)} new episodes for {show_title}")

                # Auto-download new episodes if enabled
                if enable_auto_download and show_data.get('auto_download', False):
                    logger.info(f"Auto-downloading {len(new_episodes)} episodes for {show_title}")
                    for episode in new_episodes:
                        try:
                            download_id = datetime.now().strftime('%Y%m%d_%H%M%S')
                            download_path = DOWNLOAD_DIR / download_id
                            download_path.mkdir(exist_ok=True)

                            # Perform download
                            download(
                                episode['audio_url'],
                                quiet=True,
                                save_dir=str(download_path),
                                download_id=download_id
                            )

                            # Move any produced m4a to main downloads dir (best-effort)
                            try:
                                files = os.listdir(download_path)
                                for file in files:
                                    if file.endswith('.m4a'):
                                        src_path = download_path / file
                                        dst_path = DOWNLOAD_DIR / file
                                        shutil.move(str(src_path), str(dst_path))
                            except Exception:
                                # It's fine if downloader already moved the file to the music library
                                pass

                            # Cleanup temp directory and download resources
                            shutil.rmtree(download_path, ignore_errors=True)
                            download_manager.remove_cancel_event(download_id)

                            auto_downloaded_episodes += 1
                            logger.info(f"Auto-downloaded episode: {episode.get('title', 'Unknown Title')}")
                        except Exception as e:
                            download_manager.remove_cancel_event(download_id)
                            logger.error(f"Error auto-downloading episode {episode.get('title', 'Unknown Title')}: {e}")
            else:
                logger.debug(f"No new episodes for {show_title}")
            
            processing_time = time.time() - start_time
            return ShowUpdateResult(
                show_url=show_url,
                show_title=show_title,
                success=True,
                new_episodes_count=len(new_episodes),
                auto_downloaded_count=auto_downloaded_episodes,
                error_message=None,
                processing_time=processing_time,
                show_slug=show_slug,
                new_episodes=new_episodes,
            )
            
        except Exception as e:
            processing_time = time.time() - start_time
            error_msg = str(e)
            logger.error(f"Failed to update {show_title}: {error_msg}")
            
            return ShowUpdateResult(
                show_url=show_url,
                show_title=show_title,
                success=False,
                new_episodes_count=0,
                auto_downloaded_count=auto_downloaded_episodes,
                error_message=error_msg,
                processing_time=processing_time,
                show_slug=slugify(show_url),
                new_episodes=[],
            )
