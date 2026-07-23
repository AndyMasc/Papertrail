from import_export import resources

from records.models import Record


class RecordResource(resources.ModelResource):
    class Meta:
        model = Record
