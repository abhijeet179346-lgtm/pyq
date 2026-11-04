from django import forms


class QuestionCSVForm(forms.Form):
    file = forms.FileField(help_text="UTF-8 CSV file; maximum 5 MB and 500 rows.")

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if uploaded.size > 5 * 1024 * 1024:
            raise forms.ValidationError("File must be 5 MB or smaller.")
        if not uploaded.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a .csv file.")
        return uploaded
