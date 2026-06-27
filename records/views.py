from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, redirect
from django.views.generic.base import View
from django.views.generic.list import ListView

from .models import Record
from .forms import AddRecordForm


# Create your views here.
class RecordListView(LoginRequiredMixin, ListView):
    model = Record
    template_name = "records/view_all_records.html"
    context_object_name = "records"

    def get_queryset(self):
        return Record.objects.filter(user=self.request.user)

class AddRecord(LoginRequiredMixin, View):
    model = Record
    template_name = "records/add_record.html"
    
    def get(self, request):
        form = AddRecordForm()
        return render(request, self.template_name, {"form": form})
    
    def post(self, request):
        form = AddRecordForm(request.POST)
        if form.is_valid():
            
            Record.objects.create(
                user = request.user,
                title = form.cleaned_data['title'],
                product = form.cleaned_data['product'],
                merchant = form.cleaned_data['merchant'],
                balance = form.cleaned_data['balance'],
                transaction_date = form.cleaned_data['transaction_date'],
                expiry_date = form.cleaned_data['expiry_date'],
                record_type = form.cleaned_data['record_type'],
            )
            
            return redirect("records:view_all_records")
        return render(request, self.template_name, {"form": form})