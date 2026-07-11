from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.views.generic import ListView
from records.models import Record
from django.db.models import Sum
from django.utils import timezone
from datetime import timedelta

def index(request):
    return render(request, 'core/landing_page.html')

class dashboard(LoginRequiredMixin, ListView):
    model = Record
    template_name = 'core/dashboard.html'
    context_object_name = 'records'

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user, is_active=True).order_by('-last_edited')[:5]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['expiring_soon'] = Record.objects.filter(
            user=self.request.user, 
            is_active=True,
            expiry_date__gte=timezone.now(),
            expiry_date__lte=timezone.now() + timedelta(days=30)).order_by('-date_added')[:4]

        monthly_expenses = Record.objects.filter(
            user=self.request.user, 
            date_added__month=timezone.now().month, 
            date_added__year=timezone.now().year).aggregate(total=Sum('balance'))
        
        context['monthly_expenses'] = monthly_expenses['total']
        
        return context

def privacy_policy(request):
    return render(request, 'core/privacy_policy.html')

def profile_page(request):
    return render(request, 'core/profile_page.html')
