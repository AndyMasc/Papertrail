import django_filters
from django import forms
from django.core.cache import cache
from django.utils import timezone

from .models import Record, Folder

FILTER_CHOICES_CACHE_TTL = 300


class RecordFilter(django_filters.FilterSet):
    expiring_soon = django_filters.BooleanFilter(
        method="filter_expiring_soon",
        label="Expiring within 30 days",
        field_name="is_expiring_soon",
        lookup_expr="exact",
        widget=forms.Select(choices=[(False, "Current"), (True, "Expiring Soon")]),
    )

    is_active = django_filters.BooleanFilter(
        field_name="is_active",
        lookup_expr="exact",
        widget=forms.Select(
            choices=[(None, "All"), (True, "Active"), (False, "Archived")]
        ),
    )

    record_type = django_filters.ChoiceFilter(
        field_name="record_type",
        widget=forms.Select(),
    )

    folder = django_filters.ChoiceFilter(
        method="filter_by_folder",
        empty_label="All Folders",
        widget=forms.Select(),
    )

    this_month = django_filters.BooleanFilter(
        method="filter_this_month",
        label="Records from this month",
        field_name="this_month_records",
        widget=forms.Select(choices=[(False, "All Time"), (True, "This Month")]),
    )

    is_current = django_filters.BooleanFilter(
        method="filter_is_current",
        label="Current Records",
        field_name="is_current",
        widget=forms.Select(
            choices=[(None, "All"), (True, "Current"), (False, "Past")]
        ),
    )

    class Meta:
        model = Record
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.request and self.request.user.is_authenticated:
            folder_filter = self.filters.get("folder") or self.base_filters.get(
                "folder"
            )
            if folder_filter:
                user_folders = Folder.objects.filter(
                    user=self.request.user
                ).values_list("id", "name")
                folder_filter.extra["choices"] = [("none", "All folders")] + list(
                    user_folders
                )

            cache_key = f"rt_{self.request.user.id}"
            user_record_types = cache.get(cache_key)
            if user_record_types is None:
                user_record_types = set(
                    Record.objects.filter(
                        user=self.request.user, is_active=True
                    ).values_list("record_type", flat=True)
                )
                cache.set(cache_key, user_record_types, FILTER_CHOICES_CACHE_TTL)

            all_choices = Record.RecordTypes.choices
            if user_record_types:
                filtered = [
                    (value, label)
                    for value, label in all_choices
                    if value in user_record_types
                ]
            else:
                filtered = list(all_choices)

            type_filter = self.filters.get("record_type") or self.base_filters.get(
                "record_type"
            )
            if type_filter:
                type_filter.extra["choices"] = [("", "All Types")] + filtered

    def filter_by_folder(self, queryset, name, value):
        if not value:
            return queryset

        if value == "none":
            return queryset.filter(folder__isnull=True)

        return queryset.filter(folder_id=value)

    def filter_is_current(self, queryset, name, value):
        if value:
            return queryset.filter(expiry_date__gt=timezone.now().date())
        return queryset

    def filter_expiring_soon(self, queryset, name, value):
        if value:
            today = timezone.now().date()
            return queryset.filter(
                expiry_date__lte=today + timezone.timedelta(days=30),
                expiry_date__gte=today,
            )
        return queryset

    def filter_this_month(self, queryset, name, value):
        if value:
            now = timezone.now()
            return queryset.filter(
                transaction_date__month=now.month,
                transaction_date__year=now.year,
            )
        return queryset
