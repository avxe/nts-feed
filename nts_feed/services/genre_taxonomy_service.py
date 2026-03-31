"""
Genre Taxonomy Service - Dynamic genre relationship building from Last.fm

This service:
- Fetches similar tags for core genres from Last.fm
- Builds genre families automatically (no manual maintenance)
- Computes genre compatibility/incompatibility from similarity scores
- Caches the taxonomy to disk for performance
- Provides efficient lookup methods for playlist and discovery scoring

Key improvement over static GENRE_FAMILIES:
- Automatically adapts to new genres
- Uses real-world tag similarity data
- No manual maintenance of incompatibility lists
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..runtime_paths import data_path

logger = logging.getLogger(__name__)

# Core genres to build families from
# These are the "seed" genres that we query Last.fm for
CORE_GENRES = [
    # Classical & Orchestral
    'classical', 'orchestral', 'opera', 'baroque', 'chamber music',
    'modern classical', 'neo-classical', 'minimalism',
    
    # Jazz
    'jazz', 'bebop', 'hard bop', 'cool jazz', 'free jazz',
    'jazz fusion', 'soul jazz', 'latin jazz', 'spiritual jazz',
    
    # Electronic
    'electronic', 'house', 'techno', 'ambient', 'idm',
    'drum and bass', 'dubstep', 'trance', 'electro',
    'downtempo', 'trip hop', 'chillout',
    
    # Rock & Alternative  
    'rock', 'classic rock', 'punk', 'post-punk', 'indie',
    'alternative', 'shoegaze', 'metal', 'hard rock',
    'progressive rock', 'psychedelic rock',
    
    # Hip-Hop & R&B
    'hip hop', 'rap', 'trap', 'r&b', 'neo soul',
    'conscious hip hop', 'boom bap',
    
    # Soul & Funk
    'soul', 'funk', 'disco', 'boogie', 'motown',
    'northern soul', 'deep soul',
    
    # World Music
    'reggae', 'dub', 'afrobeat', 'latin', 'brazilian',
    'bossa nova', 'salsa', 'cumbia', 'dancehall',
    
    # Pop
    'pop', 'synth pop', 'dance pop', 'art pop', 'indie pop',
    
    # Other
    'folk', 'country', 'blues', 'gospel',
    'experimental', 'noise', 'industrial', 'new wave',
    'grime', 'uk garage', 'garage',
]

# Similarity thresholds for building relationships
FAMILY_THRESHOLD = 0.5       # Genres with similarity > 0.5 are in the same family
RELATED_THRESHOLD = 0.3      # Genres with similarity > 0.3 are related
INCOMPATIBLE_THRESHOLD = 0.1 # Genres with similarity < 0.1 are potentially incompatible

# Cache settings
TAXONOMY_CACHE_FILE = 'genre_taxonomy.json'
TAXONOMY_CACHE_TTL = 7 * 24 * 3600  # 7 days


@dataclass
class GenreTaxonomy:
    """Holds the computed genre taxonomy."""
    families: Dict[str, Dict[str, List[str]]]  # family_name -> {core: [...], related: [...]}
    similarity_matrix: Dict[str, Dict[str, float]]  # genre -> genre -> similarity
    incompatibilities: Dict[str, List[str]]  # family -> [incompatible families]
    genre_to_family: Dict[str, str]  # genre -> family it belongs to
    built_at: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'families': self.families,
            'similarity_matrix': self.similarity_matrix,
            'incompatibilities': self.incompatibilities,
            'genre_to_family': self.genre_to_family,
            'built_at': self.built_at,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GenreTaxonomy':
        return cls(
            families=data.get('families', {}),
            similarity_matrix=data.get('similarity_matrix', {}),
            incompatibilities=data.get('incompatibilities', {}),
            genre_to_family=data.get('genre_to_family', {}),
            built_at=data.get('built_at', 0.0),
        )


class GenreTaxonomyService:
    """
    Service for building and querying dynamic genre taxonomy from Last.fm.
    
    Usage:
        taxonomy_service = GenreTaxonomyService(lastfm_service, cache_dir='data/')
        taxonomy = taxonomy_service.get_taxonomy()  # Loads from cache or builds
        
        # Check if genres are compatible
        affinity = taxonomy_service.compute_genre_affinity(
            artist_genres={'rock': 1.0, 'punk': 0.8},
            seed_genres=['classical', 'orchestral']
        )
    """
    
    def __init__(
        self,
        lastfm_service=None,
        cache_dir: str | None = None,
    ):
        """Initialize the service.
        
        Args:
            lastfm_service: Optional LastFmService instance (lazy-loaded if not provided)
            cache_dir: Directory to store the taxonomy cache
        """
        self._lastfm_service = lastfm_service
        self.cache_dir = Path(cache_dir) if cache_dir else data_path()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._taxonomy: Optional[GenreTaxonomy] = None
    
    @property
    def lastfm_service(self):
        """Lazy-load LastFmService."""
        if self._lastfm_service is None:
            from .lastfm_service import LastFmService
            self._lastfm_service = LastFmService()
        return self._lastfm_service
    
    def get_taxonomy(self, force_rebuild: bool = False) -> GenreTaxonomy:
        """Get the genre taxonomy, loading from cache or building if needed.
        
        Args:
            force_rebuild: If True, rebuild even if cache exists
            
        Returns:
            GenreTaxonomy object
        """
        # Return cached in-memory taxonomy
        if self._taxonomy is not None and not force_rebuild:
            return self._taxonomy
        
        # Try loading from disk cache
        if not force_rebuild:
            cached = self._load_cache()
            if cached is not None:
                self._taxonomy = cached
                return self._taxonomy
        
        # Build new taxonomy
        self._taxonomy = self.build_taxonomy()
        self._save_cache(self._taxonomy)
        
        return self._taxonomy
    
    def build_taxonomy(
        self,
        on_progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> GenreTaxonomy:
        """Build the genre taxonomy from Last.fm data.
        
        Args:
            on_progress: Optional callback(status, current, total)
            
        Returns:
            GenreTaxonomy object
        """
        logger.info("Building genre taxonomy from Last.fm...")
        
        # Step 1: Fetch similar tags for all core genres
        similarity_matrix: Dict[str, Dict[str, float]] = {}
        total = len(CORE_GENRES)
        
        for i, genre in enumerate(CORE_GENRES):
            if on_progress:
                on_progress(f"Fetching similar tags for '{genre}'", i + 1, total)
            
            # Get similar tags from Last.fm
            similar = self.lastfm_service.get_similar_tags(genre, limit=50)
            
            # Store in similarity matrix
            similarity_matrix[genre] = {}
            similarity_matrix[genre][genre] = 1.0  # Self-similarity
            
            for tag_data in similar:
                tag_name = tag_data['name'].lower().strip()
                match_score = tag_data['match']
                similarity_matrix[genre][tag_name] = match_score
                
                # Ensure symmetric relationship
                if tag_name not in similarity_matrix:
                    similarity_matrix[tag_name] = {}
                if genre not in similarity_matrix[tag_name]:
                    similarity_matrix[tag_name][genre] = match_score
            
            # Small delay to respect rate limits
            time.sleep(0.2)
        
        # Step 2: Build genre families from similarity clusters
        families, genre_to_family = self._build_families_from_similarity(similarity_matrix)
        
        # Step 3: Compute incompatibilities
        incompatibilities = self._compute_incompatibilities(similarity_matrix, genre_to_family)
        
        taxonomy = GenreTaxonomy(
            families=families,
            similarity_matrix=similarity_matrix,
            incompatibilities=incompatibilities,
            genre_to_family=genre_to_family,
            built_at=time.time(),
        )
        
        logger.info(f"Built taxonomy with {len(families)} families, {len(incompatibilities)} incompatibility rules")
        
        return taxonomy
    
    def _build_families_from_similarity(
        self,
        similarity_matrix: Dict[str, Dict[str, float]],
    ) -> Tuple[Dict[str, Dict[str, List[str]]], Dict[str, str]]:
        """Build genre families from similarity matrix using clustering.
        
        Returns:
            Tuple of (families dict, genre_to_family mapping)
        """
        # Use core genres as family anchors
        families: Dict[str, Dict[str, List[str]]] = {}
        genre_to_family: Dict[str, str] = {}
        
        # First pass: each core genre becomes a potential family
        for genre in CORE_GENRES:
            if genre not in similarity_matrix:
                continue
            
            # Family name is the core genre
            family_name = genre.replace(' ', '_').replace('-', '_')
            
            core_genres = [genre]
            related_genres = []
            
            # Find similar genres
            for other_genre, sim in similarity_matrix[genre].items():
                if other_genre == genre:
                    continue
                
                if sim >= FAMILY_THRESHOLD:
                    # Very similar - part of core family
                    if other_genre not in genre_to_family:
                        core_genres.append(other_genre)
                        genre_to_family[other_genre] = family_name
                elif sim >= RELATED_THRESHOLD:
                    # Moderately similar - related
                    related_genres.append(other_genre)
            
            genre_to_family[genre] = family_name
            
            families[family_name] = {
                'core': list(set(core_genres)),
                'related': list(set(related_genres)),
            }
        
        # Second pass: merge highly overlapping families
        families = self._merge_overlapping_families(families, similarity_matrix)
        
        # Rebuild genre_to_family after merging
        genre_to_family = {}
        for family_name, family_data in families.items():
            for g in family_data['core']:
                genre_to_family[g] = family_name
            for g in family_data['related']:
                if g not in genre_to_family:
                    genre_to_family[g] = family_name
        
        return families, genre_to_family
    
    def _merge_overlapping_families(
        self,
        families: Dict[str, Dict[str, List[str]]],
        similarity_matrix: Dict[str, Dict[str, float]],
    ) -> Dict[str, Dict[str, List[str]]]:
        """Merge families that have high overlap."""
        # Calculate family-to-family similarity
        family_names = list(families.keys())
        merged = set()
        result = {}
        
        for i, f1 in enumerate(family_names):
            if f1 in merged:
                continue
            
            # Start with this family
            merged_core = set(families[f1]['core'])
            merged_related = set(families[f1]['related'])
            
            # Check for highly similar families to merge
            for j, f2 in enumerate(family_names):
                if i >= j or f2 in merged:
                    continue
                
                # Check if core genres are similar
                f1_core = families[f1]['core'][0] if families[f1]['core'] else None
                f2_core = families[f2]['core'][0] if families[f2]['core'] else None
                
                if f1_core and f2_core:
                    sim = similarity_matrix.get(f1_core, {}).get(f2_core, 0)
                    if sim >= FAMILY_THRESHOLD:
                        # Merge f2 into f1
                        merged_core.update(families[f2]['core'])
                        merged_related.update(families[f2]['related'])
                        merged.add(f2)
            
            # Remove core genres from related
            merged_related -= merged_core
            
            result[f1] = {
                'core': sorted(list(merged_core)),
                'related': sorted(list(merged_related)),
            }
            merged.add(f1)
        
        return result
    
    def _compute_incompatibilities(
        self,
        similarity_matrix: Dict[str, Dict[str, float]],
        genre_to_family: Dict[str, str],
    ) -> Dict[str, List[str]]:
        """Compute which families are incompatible based on low similarity."""
        incompatibilities: Dict[str, Set[str]] = {}
        
        # Get unique families
        families = set(genre_to_family.values())
        
        for family in families:
            incompatibilities[family] = set()
        
        # For each pair of families, check average similarity
        family_list = list(families)
        for i, f1 in enumerate(family_list):
            for j, f2 in enumerate(family_list):
                if i >= j:
                    continue
                
                # Get representative genres for each family
                f1_genres = [g for g, f in genre_to_family.items() if f == f1][:3]
                f2_genres = [g for g, f in genre_to_family.items() if f == f2][:3]
                
                if not f1_genres or not f2_genres:
                    continue
                
                # Calculate average cross-family similarity
                total_sim = 0.0
                count = 0
                for g1 in f1_genres:
                    for g2 in f2_genres:
                        sim = similarity_matrix.get(g1, {}).get(g2, 0)
                        total_sim += sim
                        count += 1
                
                avg_sim = total_sim / max(count, 1)
                
                # If very low similarity, mark as incompatible
                if avg_sim < INCOMPATIBLE_THRESHOLD:
                    incompatibilities[f1].add(f2)
                    incompatibilities[f2].add(f1)
        
        # Convert sets to sorted lists
        return {k: sorted(list(v)) for k, v in incompatibilities.items()}
    
    def get_genre_family(self, genre: str) -> Optional[str]:
        """Get which family a genre belongs to.
        
        Args:
            genre: Genre name
            
        Returns:
            Family name or None if not found
        """
        taxonomy = self.get_taxonomy()
        genre_norm = genre.lower().strip()
        
        # Direct lookup
        if genre_norm in taxonomy.genre_to_family:
            return taxonomy.genre_to_family[genre_norm]
        
        # Fuzzy match - check if genre is substring of any known genre
        for known_genre, family in taxonomy.genre_to_family.items():
            if genre_norm in known_genre or known_genre in genre_norm:
                return family
        
        return None
    
    def get_similar_genres(self, genre: str, min_similarity: float = 0.3) -> List[Tuple[str, float]]:
        """Get genres similar to the given genre.
        
        Args:
            genre: Genre name
            min_similarity: Minimum similarity threshold
            
        Returns:
            List of (genre_name, similarity_score) tuples
        """
        taxonomy = self.get_taxonomy()
        genre_norm = genre.lower().strip()
        
        if genre_norm not in taxonomy.similarity_matrix:
            return []
        
        similar = []
        for other_genre, sim in taxonomy.similarity_matrix[genre_norm].items():
            if sim >= min_similarity and other_genre != genre_norm:
                similar.append((other_genre, sim))
        
        # Sort by similarity descending
        similar.sort(key=lambda x: -x[1])
        
        return similar
    
    def compute_genre_affinity(
        self,
        artist_genres: Dict[str, float],
        seed_genres: List[str],
        require_match: bool = True,
    ) -> Tuple[float, bool, List[str]]:
        """Compute how well an artist's genres align with seed genres.
        
        This centralizes genre-affinity scoring for discovery and taxonomy use cases.
        
        Args:
            artist_genres: Dict mapping genre name to weight (0.0-1.0)
            seed_genres: List of target genre names for the recommendation seed
            require_match: If True, return 0.0 for artists with no matching genres
            
        Returns:
            Tuple of (affinity_score, has_conflict, matched_genres)
        """
        if not artist_genres:
            return (0.5, False, [])
        
        taxonomy = self.get_taxonomy()
        
        # Find families for seed genres
        seed_families: Set[str] = set()
        for sg in seed_genres:
            family = self.get_genre_family(sg)
            if family:
                seed_families.add(family)
        
        # Find families for artist genres
        artist_families: Dict[str, float] = {}
        for ag, weight in artist_genres.items():
            family = self.get_genre_family(ag)
            if family:
                if family not in artist_families or weight > artist_families[family]:
                    artist_families[family] = weight
        
        # Check for conflicts
        has_conflict = False
        for seed_family in seed_families:
            incompatible = taxonomy.incompatibilities.get(seed_family, [])
            for artist_family in artist_families.keys():
                if artist_family in incompatible:
                    has_conflict = True
                    break
        
        # Calculate affinity score
        matched_genres: List[str] = []
        affinity_score = 0.0
        
        # Direct genre matching using similarity matrix
        for ag, weight in artist_genres.items():
            ag_norm = ag.lower().strip()
            for sg in seed_genres:
                sg_norm = sg.lower().strip()
                
                # Check similarity matrix
                sim = taxonomy.similarity_matrix.get(ag_norm, {}).get(sg_norm, 0)
                if sim > 0:
                    matched_genres.append(ag)
                    affinity_score = max(affinity_score, weight * sim)
                    break
                
                # Fallback: substring match
                if ag_norm == sg_norm or sg_norm in ag_norm or ag_norm in sg_norm:
                    matched_genres.append(ag)
                    affinity_score = max(affinity_score, weight)
                    break
        
        # Family-level matching
        if not matched_genres and seed_families:
            for artist_family, weight in artist_families.items():
                if artist_family in seed_families:
                    affinity_score = max(affinity_score, weight * 0.8)
                    matched_genres.append(f"family:{artist_family}")
        
        # If no match and require_match, return 0
        if not matched_genres and require_match and artist_genres:
            affinity_score = 0.0
        
        # Penalize conflicts
        if has_conflict:
            affinity_score *= 0.1
        
        return (affinity_score, has_conflict, list(set(matched_genres)))
    
    def filter_genres_by_keyword_relevance(
        self,
        genres: List[str],
        seed_keywords: List[str],
    ) -> List[str]:
        """Filter genres to only those relevant to the target keywords.
        
        Args:
            genres: List of genre names to filter
            seed_keywords: Keywords defining the target selection
            
        Returns:
            Filtered list of genres relevant to the target keywords
        """
        if not genres or not seed_keywords:
            return genres
        
        taxonomy = self.get_taxonomy()
        
        # Find families represented by the target keywords
        seed_families: Set[str] = set()
        for kw in seed_keywords:
            family = self.get_genre_family(kw)
            if family:
                seed_families.add(family)
        
        if not seed_families:
            return genres
        
        # Get incompatible families
        incompatible_families: Set[str] = set()
        for family in seed_families:
            incompatible_families.update(taxonomy.incompatibilities.get(family, []))
        
        # Filter
        filtered: List[str] = []
        for genre in genres:
            genre_family = self.get_genre_family(genre)
            
            if genre_family in seed_families:
                filtered.append(genre)
            elif genre_family is None:
                # Unknown genre - check keyword match
                genre_norm = genre.lower().strip()
                for kw in seed_keywords:
                    kw_norm = kw.lower().strip()
                    if kw_norm in genre_norm or genre_norm in kw_norm:
                        filtered.append(genre)
                        break
            elif genre_family not in incompatible_families:
                # Not incompatible - check similarity
                for kw in seed_keywords:
                    kw_norm = kw.lower().strip()
                    sim = taxonomy.similarity_matrix.get(genre.lower(), {}).get(kw_norm, 0)
                    if sim >= RELATED_THRESHOLD:
                        filtered.append(genre)
                        break
        
        return filtered
    
    def _load_cache(self) -> Optional[GenreTaxonomy]:
        """Load taxonomy from disk cache."""
        cache_path = self.cache_dir / TAXONOMY_CACHE_FILE
        
        if not cache_path.exists():
            return None
        
        try:
            # Check if cache is still valid
            file_age = time.time() - cache_path.stat().st_mtime
            if file_age > TAXONOMY_CACHE_TTL:
                logger.info("Genre taxonomy cache expired")
                return None
            
            with open(cache_path, 'r') as f:
                data = json.load(f)
            
            taxonomy = GenreTaxonomy.from_dict(data)
            logger.info(f"Loaded genre taxonomy from cache ({len(taxonomy.families)} families)")
            return taxonomy
            
        except Exception as e:
            logger.warning(f"Failed to load taxonomy cache: {e}")
            return None
    
    def _save_cache(self, taxonomy: GenreTaxonomy) -> None:
        """Save taxonomy to disk cache."""
        cache_path = self.cache_dir / TAXONOMY_CACHE_FILE
        
        try:
            with open(cache_path, 'w') as f:
                json.dump(taxonomy.to_dict(), f, indent=2)
            logger.info("Saved genre taxonomy to cache")
        except Exception as e:
            logger.warning(f"Failed to save taxonomy cache: {e}")
    
    def clear_cache(self) -> None:
        """Clear the taxonomy cache."""
        cache_path = self.cache_dir / TAXONOMY_CACHE_FILE
        if cache_path.exists():
            cache_path.unlink()
        self._taxonomy = None
        logger.info("Cleared genre taxonomy cache")
