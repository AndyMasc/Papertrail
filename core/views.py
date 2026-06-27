from django.shortcuts import render
from django.views.generic import ListView
from documents.models import document_data

def index(request):
    return render(request, 'core/home.html')

class dashboard(ListView):
    model = document_data
    template_name = 'core/dashboard.html'
    context_object_name = 'documents'

    def get_queryset(self):
        return super().get_queryset().order_by('-date_added')[:5]