import django_filters
from django import forms
from .models import DocumentData
from django.core.cache import cache

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
            cache_key = f"doc_extensions_{self.request.user.id}"
            existing_extensions = cache.get(cache_key)
            if existing_extensions is None:
                existing_extensions = list(
                    DocumentData.objects.filter(user=self.request.user)
                    .exclude(file_extension="")
                    .exclude(file_extension__isnull=True)
                    .values_list("file_extension", flat=True)
                    .distinct()
                    .order_by("file_extension")
                )
                cache.set(cache_key, existing_extensions, FILTER_CHOICES_CACHE_TTL)
        else:
            existing_extensions = (
                self.queryset.exclude(file_extension="")
                .exclude(file_extension__isnull=True)
                .values_list("file_extension", flat=True)
                .distinct()
                .order_by("file_extension")
            )

        self.filters["file_type"].extra["choices"] = [("", "All File Types")] + [
            (ext.lower(), ext.upper())
            for ext in existing_extensions
            if ext and ext.isalnum() and len(ext) <= 10
        ]

    def filter_by_status(self, queryset, name, value):
        if value == "orphaned":
            return queryset.filter(associated_record__isnull=True)
        elif value == "linked":
            return queryset.filter(associated_record__isnull=False)
        return queryset
