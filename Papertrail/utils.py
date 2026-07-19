import hashlib

from django.core.cache import cache
from django.core.paginator import Paginator
from django.utils.functional import cached_property

PAGINATOR_COUNT_CACHE_TTL = 60


class CachedPaginator(Paginator):

    @cached_property
    def count(self):
        if not hasattr(self.object_list, "query"):
            return Paginator.count.__get__(self, type(self))
        cache_key = self._make_count_cache_key()
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        result = Paginator.count.__get__(self, type(self))
        cache.set(cache_key, result, PAGINATOR_COUNT_CACHE_TTL)
        return result

    def _make_count_cache_key(self) -> str:
        where = str(self.object_list.query.where)
        raw = f"pg:{self.object_list.query.model._meta.db_table}:{where}"
        return f"pg:{hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()}"
