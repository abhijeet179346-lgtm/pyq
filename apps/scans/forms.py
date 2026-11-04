from django import forms


class ScanUploadForm(forms.Form):
    file = forms.FileField()
    source = forms.CharField(max_length=240)
    license = forms.CharField(max_length=160)

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > 100 * 1024 * 1024:
            raise forms.ValidationError("Maximum file size is 100 MB.")
        name = file.name.lower()
        if not name.endswith((".pdf", ".png", ".jpg", ".jpeg")):
            raise forms.ValidationError("Upload a PDF, PNG, or JPEG scan.")
        header = file.read(8)
        file.seek(0)
        valid = (name.endswith(".pdf") and header.startswith(b"%PDF-")) or (
            name.endswith(".png") and header.startswith(b"\x89PNG\r\n\x1a\n")) or (
            name.endswith((".jpg", ".jpeg")) and header.startswith(b"\xff\xd8\xff"))
        if not valid:
            raise forms.ValidationError("File content does not match its extension.")
        return file
