from datetime import timedelta
from django import forms
from django.utils import timezone
import django_filters
from .models import Record


class RecordFilter(django_filters.FilterSet):
    expiring_soon = django_filters.BooleanFilter(
        method="filter_expiring_soon", label="Expiring within 30 days",
        field_name="is_expiring_soon",
        lookup_expr="exact",
        widget=forms.Select(
            choices=[(False, "Current"), (True, "Expiring Soon")]
        ),
    )

    is_active = django_filters.BooleanFilter(
        field_name="is_active",
        lookup_expr="exact",
        widget=forms.Select(
            choices=[(True, "Active"), (False, "Archived")]
        ),
    )

    record_type = django_filters.ChoiceFilter(
        field_name="record_type",
        widget=forms.Select(),
    )

    class Meta:
        model = Record
        # Left empty because all filters are explicitly declared above
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.request and self.request.user.is_authenticated:
            user_record_types = (
                Record.objects.filter(user=self.request.user)
                .values_list("record_type", flat=True)
                .distinct()
            )
            
            filtered_choices = [
                (choice_value, choice_label)
                for choice_value, choice_label in Record.RecordTypes.choices
                if choice_value in user_record_types
            ]

            self.filters["record_type"].extra["choices"] = [
                ("", "All Types")
            ] + filtered_choices

    def filter_expiring_soon(self, queryset, name, value):
        if value:
            month_from_now = timezone.now().date() + timedelta(days=30)
            return queryset.filter(
                expiry_date__lte=month_from_now,
                expiry_date__gte=timezone.now().date(),
            ).order_by("-date_added")

        return queryset