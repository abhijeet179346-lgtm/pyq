from django import forms


class TutorPhotoForm(forms.Form):
    topic = forms.IntegerField(min_value=1)
    image = forms.FileField()

    def clean_image(self):
        image = self.cleaned_data["image"]
        if image.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Maximum image size is 10 MB.")
        name = image.name.lower()
        header = image.read(8)
        image.seek(0)
        if not ((name.endswith(".png") and header.startswith(b"\x89PNG\r\n\x1a\n")) or
                (name.endswith((".jpg", ".jpeg")) and header.startswith(b"\xff\xd8\xff"))):
            raise forms.ValidationError("Upload a PNG or JPEG image.")
        return image
