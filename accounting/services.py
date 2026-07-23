from .resources import RecordResource


def export_to_excel():
    dataset = RecordResource().export()
    excel_data = dataset.xlsx

    return excel_data
