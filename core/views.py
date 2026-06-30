from django.shortcuts import render
from django.views.generic import ListView
from records.models import Record

def index(request):
    return render(request, 'core/home.html')

class dashboard(ListView):
    model = Record
    template_name = 'core/dashboard.html'
    context_object_name = 'records'

    def get_queryset(self):
        return super().get_queryset().order_by('date_added')[:5]