class GenreSearch {
    constructor() {
        this.searchInput = document.getElementById('genreSearch');
        if (!this.searchInput) {
            // Not on a page with genre search - silently exit
            return;
        }
        this.inputContainer = this.searchInput.closest('.search-input-container');
        if (!this.inputContainer) {
            console.error('Required elements not found');
            return;
        }
        
        this.activeGenres = new Set();
        this.allGenres = new Set();
        this.currentFocus = -1;
        this.genreRelations = new Map();
        
        // Initialize the search box container
        this.searchBox = this.inputContainer.closest('.search-box');
        this.searchContainer = this.searchBox.closest('.search-container');
        
        // Set initial width
        this.adjustInputWidth();
        
        this.init();
    }
    
    init() {
        // Collect all unique genres and their counts
        this.genreCounts = {};
        document.querySelectorAll('.genre-tag').forEach(tag => {
            const genre = tag.textContent.trim();
            this.allGenres.add(genre);
            this.genreCounts[genre] = (this.genreCounts[genre] || 0) + 1;
            
            // Make genre tags clickable for filtering
            tag.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                this.toggleGenre(genre);
            });
            tag.style.cursor = 'pointer';
            tag.title = 'Click to filter by this genre';
        });
        
        // Create suggestions container
        this.createSuggestionsContainer();
        
        // Add event listeners
        this.searchInput.addEventListener('input', () => this.handleInput());
        this.searchInput.addEventListener('keydown', (e) => this.handleKeyDown(e));
        
        // Close suggestions when clicking outside
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.search-box')) {
                this.hideSuggestions();
            }
        });
        
        // Add resize observer with debounce for better performance
        let resizeTimeout;
        const resizeObserver = new ResizeObserver(() => {
            if (resizeTimeout) clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(() => this.adjustInputWidth(), 100);
        });
        resizeObserver.observe(this.inputContainer);
        
        // Add focus event listener
        this.searchInput.addEventListener('focus', () => this.handleFocus());
        
        // Build genre relations map
        document.querySelectorAll('.show-item, .episode-item').forEach(element => {
            const elementGenres = Array.from(element.querySelectorAll('.genre-tag'))
                .map(tag => tag.textContent.trim());
            
            // For each genre, record which other genres appear with it
            elementGenres.forEach(genre => {
                if (!this.genreRelations.has(genre)) {
                    this.genreRelations.set(genre, new Map());
                }
                
                const relations = this.genreRelations.get(genre);
                elementGenres.forEach(relatedGenre => {
                    if (relatedGenre !== genre) {
                        relations.set(relatedGenre, (relations.get(relatedGenre) || 0) + 1);
                    }
                });
            });
        });
    }
    
    createSuggestionsContainer() {
        this.suggestionsContainer = document.createElement('div');
        this.suggestionsContainer.className = 'genre-suggestions';
        this.searchInput.parentNode.appendChild(this.suggestionsContainer);
    }
    
    handleInput() {
        const value = this.searchInput.value.toLowerCase().trim();
        
        if (!value || this.activeGenres.size >= 3) {
            this.hideSuggestions();
            return;
        }
        
        let availableGenres = Array.from(this.allGenres);
        
        // Filter by genres that appear together with selected genres
        if (this.activeGenres.size > 0) {
            availableGenres = this.getCompatibleGenres();
        }
        
        const matchingGenres = availableGenres
            .filter(genre => 
                genre.toLowerCase().includes(value) && 
                !this.activeGenres.has(genre)
            )
            .sort((a, b) => this.genreCounts[b] - this.genreCounts[a]);
        
        this.showSuggestions(matchingGenres, value);
    }
    
    handleSearch() {
        const value = this.searchInput.value.trim();
        if (!value) return;
        
        const matchingGenre = Array.from(this.allGenres)
            .find(genre => 
                genre.toLowerCase().includes(value.toLowerCase()) && 
                !this.activeGenres.has(genre)
            );
            
        if (matchingGenre) {
            this.toggleGenre(matchingGenre);
            this.searchInput.value = '';
            this.hideSuggestions();
        }
    }
    
    showSuggestions(genres, searchValue) {
        this.suggestionsContainer.innerHTML = '';
        
        if (genres.length === 0) {
            this.suggestionsContainer.style.display = 'none';
            return;
        }
        
        genres.forEach(genre => {
            const item = document.createElement('div');
            item.className = 'genre-suggestion';
            
            const textSpan = document.createElement('span');
            const countSpan = document.createElement('span');
            countSpan.className = 'genre-count';
            countSpan.textContent = this.genreCounts[genre];
            
            // Highlight matching text
            const index = genre.toLowerCase().indexOf(searchValue.toLowerCase());
            if (index >= 0) {
                textSpan.innerHTML = genre.substring(0, index) +
                    `<strong>${genre.substring(index, index + searchValue.length)}</strong>` +
                    genre.substring(index + searchValue.length);
            } else {
                textSpan.textContent = genre;
            }
            
            item.appendChild(textSpan);
            item.appendChild(countSpan);
            
            item.addEventListener('click', () => {
                this.toggleGenre(genre);
                this.searchInput.value = '';
                this.hideSuggestions();
            });
            
            this.suggestionsContainer.appendChild(item);
        });
        
        this.suggestionsContainer.style.display = 'block';
    }
    
    hideSuggestions() {
        this.suggestionsContainer.style.display = 'none';
        this.currentFocus = -1;
    }
    
    handleKeyDown(e) {
        const items = this.suggestionsContainer.getElementsByClassName('genre-suggestion');
        
        if (e.key === 'Enter') {
            e.preventDefault();
            if (this.currentFocus >= 0 && items[this.currentFocus]) {
                // Get the text content of the selected suggestion (without the count)
                const selectedGenre = items[this.currentFocus].querySelector('span').textContent;
                this.toggleGenre(selectedGenre);
                this.searchInput.value = '';
                this.hideSuggestions();
            } else {
                this.handleSearch();
            }
        }
        else if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (this.currentFocus < items.length - 1) {
                this.currentFocus++;
                this.updateActiveSuggestion(items);
            }
        }
        else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (this.currentFocus > 0) {
                this.currentFocus--;
                this.updateActiveSuggestion(items);
            }
        }
        else if (e.key === 'Escape') {
            this.hideSuggestions();
            this.searchInput.value = '';
        }
    }
    
    updateActiveSuggestion(items) {
        Array.from(items).forEach((item, index) => {
            item.classList.toggle('active', index === this.currentFocus);
            if (index === this.currentFocus) {
                // Ensure the selected item is visible in the suggestions container
                item.scrollIntoView({ block: 'nearest' });
            }
        });
    }
    
    toggleGenre(genre) {
        console.log('Toggling genre:', genre);
        
        if (!this.activeGenres.has(genre) && this.activeGenres.size >= 3) {
            console.log('Maximum genres reached (3)');
            return;
        }
        
        if (this.activeGenres.has(genre)) {
            console.log('Removing genre:', genre);
            this.activeGenres.delete(genre);
            this.removeActiveTag(genre);
        } else {
            console.log('Adding genre:', genre);
            this.activeGenres.add(genre);
            this.addActiveTag(genre);
        }
        
        this.filterShows();
        
        // Update suggestions if input is focused
        if (document.activeElement === this.searchInput) {
            this.handleFocus();
        }
    }
    
    addActiveTag(genre) {
        // Create the tag element
        const tag = document.createElement('div');
        tag.className = 'inline-tag';
        tag.innerHTML = `
            <span class="tag-text">${genre}</span>
            <button class="remove-tag" aria-label="Remove ${genre} filter">×</button>
        `;
        
        // Add event listener to the remove button
        tag.querySelector('.remove-tag').addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleGenre(genre);
        });
        
        // First update the container width to accommodate the new tag
        // This prevents the staggered animation
        const tagWidth = genre.length * 8 + 40; // Estimate tag width based on text length
        const currentWidth = parseInt(this.inputContainer.style.width || 0);
        const newWidth = currentWidth + tagWidth;
        this.inputContainer.style.width = newWidth + 'px';
        
        // Then insert the tag
        this.inputContainer.insertBefore(tag, this.searchInput);
        
        // Clear the placeholder when at least one tag is added
        if (this.activeGenres.size > 0) {
            this.searchInput.placeholder = '';
        }
        
        // Update layout after a short delay to ensure smooth animation
        setTimeout(() => {
            this.adjustInputWidth();
            // Focus the input after adding a tag
            this.searchInput.focus();
        }, 50);
    }
    
    removeActiveTag(genre) {
        const tags = this.inputContainer.querySelectorAll('.inline-tag');
        tags.forEach(tag => {
            if (tag.querySelector('.tag-text').textContent === genre) {
                tag.remove();
            }
        });
        
        // Restore placeholder if no tags are left
        if (this.activeGenres.size === 0) {
            this.searchInput.placeholder = 'Genre';
        }
        
        // Update layout immediately
        this.adjustInputWidth();
        
        // Focus the input after removing a tag
        this.searchInput.focus();
    }
    
    adjustInputWidth() {
        const minWidth = 80;
        const maxWidth = 200;
        
        // Get the container's padding
        const containerStyle = window.getComputedStyle(this.inputContainer);
        const paddingLeft = parseFloat(containerStyle.paddingLeft);
        const paddingRight = parseFloat(containerStyle.paddingRight);
        
        // Calculate total width of all tags
        let tagsWidth = 0;
        const tags = this.inputContainer.querySelectorAll('.inline-tag');
        
        if (tags.length > 0) {
            tags.forEach(tag => {
                tagsWidth += tag.offsetWidth + 8; // Add some margin
            });
        }
        
        // Calculate input width - fixed width for better performance
        const inputWidth = 100;
        this.searchInput.style.width = inputWidth + 'px';
        
        // Calculate container width based on tags and input
        const containerMinWidth = tagsWidth + inputWidth + paddingLeft + paddingRight + 20; // Add some extra space
        
        // Set container width to accommodate all tags and input
        // But don't exceed the parent container width
        const parentWidth = this.searchBox.clientWidth;
        const containerWidth = Math.min(parentWidth - 10, containerMinWidth);
        
        // Update the input container width - use requestAnimationFrame for smoother transitions
        requestAnimationFrame(() => {
            this.inputContainer.style.width = containerWidth + 'px';
            
            // Also update the search box width if needed
            if (this.searchContainer && containerMinWidth > parentWidth) {
                const searchBoxMinWidth = containerWidth + 10; // Add some margin
                const searchContainerWidth = this.searchContainer.clientWidth;
                
                // Only expand the search box if needed
                if (searchBoxMinWidth > searchContainerWidth) {
                    const newWidth = Math.min(window.innerWidth - 40, searchBoxMinWidth);
                    this.searchContainer.style.width = newWidth + 'px';
                }
            }
        });
    }
    
    filterShows() {
        const showElements = document.querySelectorAll('.show-item, .episode-item');
        showElements.forEach(element => {
            const genres = Array.from(element.querySelectorAll('.genre-tag'))
                .map(tag => tag.textContent.trim());
            
            const matchesActive = this.activeGenres.size === 0 || 
                Array.from(this.activeGenres).every(activeGenre => 
                    genres.includes(activeGenre)
                );
            
            if (matchesActive) {
                element.classList.remove('hidden');
                element.style.display = '';  // Reset to default display
                element.style.animation = 'tagAppear 0.15s ease-out forwards';
            } else {
                element.classList.add('hidden');
                element.style.display = 'none';  // Explicitly hide the element
            }
        });
        
        // Log for debugging
        console.log('Filter applied with genres:', Array.from(this.activeGenres));
        console.log('Visible items:', document.querySelectorAll('.show-item:not(.hidden), .episode-item:not(.hidden)').length);
        console.log('Hidden items:', document.querySelectorAll('.show-item.hidden, .episode-item.hidden').length);
    }
    
    handleFocus() {
        if (this.activeGenres.size >= 3) {
            return;
        }
        
        if (!this.searchInput.value) {
            let availableGenres = Array.from(this.allGenres);
            
            // Filter by compatible genres if we have active genres
            if (this.activeGenres.size > 0) {
                availableGenres = this.getCompatibleGenres();
            }
            
            availableGenres = availableGenres
                .filter(genre => !this.activeGenres.has(genre))
                .sort((a, b) => this.genreCounts[b] - this.genreCounts[a]);
                
            this.showSuggestions(availableGenres, '');
        }
    }
    
    getCompatibleGenres() {
        // Start with the first selected genre's relations
        let compatibleGenres = new Map();
        const activeGenresArray = Array.from(this.activeGenres);
        
        // Get genres that appear with all selected genres
        activeGenresArray.forEach(activeGenre => {
            const relations = this.genreRelations.get(activeGenre);
            if (!relations) return;
            
            if (compatibleGenres.size === 0) {
                compatibleGenres = new Map(relations);
            } else {
                // Keep only genres that appear with all selected genres
                for (const [genre, count] of compatibleGenres) {
                    if (!relations.has(genre)) {
                        compatibleGenres.delete(genre);
                    }
                }
            }
        });
        
        return Array.from(compatibleGenres.keys());
    }
}

// Initialize genre search
function initGenreSearch() {
    console.log('Initializing GenreSearch');
    const genreSearch = new GenreSearch();
    
    // Add to window for debugging
    window.genreSearch = genreSearch;
    
    // Only log if successfully initialized
    if (genreSearch.allGenres) {
        console.log('GenreSearch initialized with', genreSearch.allGenres.size, 'genres');
    }
}

// Expose for SPA router
window.initGenreSearchHandlers = initGenreSearch;
