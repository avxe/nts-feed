"""Dedicated Discover API blueprint."""

from flask import Blueprint

from . import api_mixtape as discover_impl

bp = Blueprint('api_discover', __name__)

bp.add_url_rule('/api/discover', endpoint='api_discover', view_func=discover_impl.api_discover)
bp.add_url_rule(
    '/api/discover/surprise',
    endpoint='api_discover_surprise',
    view_func=discover_impl.api_discover_surprise,
    methods=['POST'],
)
bp.add_url_rule(
    '/api/discover/genre/<genre_slug>',
    endpoint='api_discover_genre',
    view_func=discover_impl.api_discover_genre,
)
bp.add_url_rule(
    '/api/discover/next-up',
    endpoint='api_discover_next_up',
    view_func=discover_impl.api_discover_next_up,
)
bp.add_url_rule(
    '/api/discover/next-up/state',
    endpoint='api_discover_next_up_state',
    view_func=discover_impl.api_discover_next_up_state,
    methods=['POST'],
)

_build_discover_state = discover_impl._build_discover_state
_build_genre_shelf_on_demand = discover_impl._build_genre_shelf_on_demand
_choose_reason_label = discover_impl._choose_reason_label
_empty_discover_payload = discover_impl._empty_discover_payload
_empty_discover_state = discover_impl._empty_discover_state
_empty_listening_summary = discover_impl._empty_listening_summary
_get_cached_discover_bundle = discover_impl._get_cached_discover_bundle
_get_discover_cache_store = discover_impl._get_discover_cache_store
_get_discover_cache_ttl_seconds = discover_impl._get_discover_cache_ttl_seconds
_get_fresh_continue_listening = discover_impl._get_fresh_continue_listening
_load_discover_catalog = discover_impl._load_discover_catalog
_pick_diverse_candidates = discover_impl._pick_diverse_candidates
_serialize_episode_card = discover_impl._serialize_episode_card
_slugify_genre = discover_impl._slugify_genre
