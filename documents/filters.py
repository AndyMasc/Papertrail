import django_filters
from django import forms
from django.core.cache import cache

from .models import DocumentData

FILTER_CHOICES_CACHE_TTL = 300


class DocumentFilter(django_filters.FilterSet):
    file_type = django_filters.ChoiceFilter(
        field_name="file_extension",
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

        if self.request and self.request.user.is_authenticated:
            cache_key = f"de_{self.request.user.id}"
            extensions = cache.get(cache_key)
            if extensions is None:
                extensions = list(
                    DocumentData.objects.filter(user=self.request.user)
                    .exclude(file_extension="")
                    .exclude(file_extension__isnull=True)
                    .values_list("file_extension", flat=True)
                    .distinct()
                )
                cache.set(cache_key, extensions, FILTER_CHOICES_CACHE_TTL)
        else:
            extensions = (
                DocumentData.objects.filter(
                    file_extension__isnull=False,
                )
                .exclude(file_extension="")
                .values_list("file_extension", flat=True)
                .distinct()
            )

        self.filters["file_type"].extra["choices"] = [("", "All File Types")] + [
            (ext.lower(), ext.upper())
            for ext in extensions
            if ext and ext.isalnum() and len(ext) <= 10
        ]

    def filter_by_status(self, queryset, name, value):
        if value == "orphaned":
            return queryset.filter(associated_record__isnull=True)
        elif value == "linked":
            return queryset.filter(associated_record__isnull=False)
        return queryset
