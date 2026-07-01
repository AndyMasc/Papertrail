from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.views.generic import ListView
from records.models import Record
from django.db.models import Count
from django.utils import timezone
from datetime import timedelta

def index(request):
    return render(request, 'core/home.html')

class dashboard(LoginRequiredMixin, ListView):
    model = Record
    template_name = 'core/dashboard.html'
    context_object_name = 'records'

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user).order_by('-last_edited')[:4]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['expiring_soon'] = Record.objects.filter(user=self.request.user, expiry_date__lte=timezone.now() + timedelta(days=30)).order_by('-date_added')[:4]
        
        popular_dict = Record.objects.filter(user=self.request.user).values('record_type').annotate(count=Count('record_type')).order_by('-count').first()
        if popular_dict:
            temp_record = Record(record_type=popular_dict['record_type'])
            popular_dict['display_name'] = temp_record.get_record_type_display()
        context['most_popular_record_type'] = popular_dict
        
        return context