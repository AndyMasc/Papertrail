import django_filters
from django import forms
from django.core.cache import cache

from .models import DocumentData

FILTER_CHOICES_CACHE_TTL = 300


class DocumentFilter(django_filters.FilterSet):
    file_type = django_filters.ChoiceFilter(
        field_name="file_extension",
        lookup_expr="iexact",
        choices=(),
        widget=forms.Select(),
        label="File Type",
    )

    status = django_filters.ChoiceFilter(
        choices=(
            ("orphaned", "Orphaned (Unlinked)"),
            ("linked", "Associated Records"),
        ),
        method="filter_by_status",
        widget=forms.Select(),
        label="Status",
    )

    class Meta:
        model = DocumentData
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        extensions = self._get_cached_extensions()
        self.filters["file_type"].extra["choices"] = [("", "All File Types")] + [
            (ext.lower(), ext.upper())
            for ext in extensions
            if ext and ext.isalnum() and len(ext) <= 10
        ]

    def _get_cached_extensions(self):
        if self.request and self.request.user.is_authenticated:
            cache_key = f"de_v2_{self.request.user.id}"
            extensions = cache.get(cache_key)
            if extensions is None:
                raw = DocumentData.objects.filter(
                    user=self.request.user
                ).values_list("file_extension", flat=True)
                seen = set()
                result = []
                for ext in raw:
                    if not ext:
                        continue
                    normalized = ext.strip().lower()[:10]
                    if normalized and normalized not in seen:
                        seen.add(normalized)
                        result.append(normalized)
                extensions = sorted(result)
                cache.set(cache_key, extensions, FILTER_CHOICES_CACHE_TTL)
            return extensions
        return []

    def filter_by_status(self, queryset, name, value):
        if value == "orphaned":
            return queryset.filter(associated_record__isnull=True)
        elif value == "linked":
            return queryset.filter(associated_record__isnull=False)
        return queryset
