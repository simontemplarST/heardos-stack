from django.template.loader import render_to_string
from django.utils import timezone
from datetime import timedelta
from authentik.core.models import User
u=User.objects.get(username="alice")
base={"user":u,"url":"https://auth.example.com/if/flow/default-recovery-flow/?token=EXAMPLE-TOKEN","expires":timezone.now()+timedelta(minutes=30),"token":"483920","host":"auth.example.com","title":"New sign-in from a new device","body":"Your account alice signed in from a device we have not seen before. If this was you, no action is needed.","link":{"target":"https://auth.example.com/if/user/","label":"Review sessions"},"key_value":{"When":"16 Oct 2026, 01:10","Where":"Springfield, Illinois","Device":"Firefox on Linux","Address":"198.51.100.72"},"source":None}
for n in ("password_reset","account_confirmation","email_otp","event_notification","invitation","setup"):
    h=render_to_string("email/%s.html"%n, base); t=render_to_string("email/%s.txt"%n, base)
    open("/tmp/prev-%s.html"%n,"w").write(h); open("/tmp/prev-%s.txt"%n,"w").write(t)
    print("%-22s html %5d B  txt %4d B  brand:%s  stock-footer:%s" % (n, len(h), len(t), "brand/logo-dark.png" in h, ("goauthentik" in h) or ("Powered by" in h)))
