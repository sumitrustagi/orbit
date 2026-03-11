from flask import Blueprint, Response
import os

auth_bp = Blueprint("auth", __name__, template_folder="../templates")

@auth_bp.route("/saml/metadata")
def saml_metadata():
    """
    Serve SAML SP metadata XML for download by the IdP admin.
    Full SAML logic is implemented in Section 4.
    """
    fqdn = os.environ.get("SERVER_FQDN", "localhost")
    metadata_xml = f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
  entityID="https://{fqdn}/auth/saml/metadata">
  <md:SPSSODescriptor
    AuthnRequestsSigned="false"
    WantAssertionsSigned="true"
    protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:NameIDFormat>
      urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress
    </md:NameIDFormat>
    <md:AssertionConsumerService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
      Location="https://{fqdn}/auth/saml/acs"
      index="1"/>
  </md:SPSSODescriptor>
</md:EntityDescriptor>"""
    return Response(metadata_xml, mimetype="application/xml",
                    headers={"Content-Disposition": "attachment; filename=orbit-sp-metadata.xml"})

@auth_bp.route("/login")
def login():
    return "Login page — implemented in Section 4", 200

@auth_bp.route("/logout")
def logout():
    return "Logout — implemented in Section 4", 200

@auth_bp.route("/oidc/callback")
def oidc_callback():
    return "OIDC callback — implemented in Section 4", 200

@auth_bp.route("/saml/acs", methods=["POST"])
def saml_acs():
    return "SAML ACS — implemented in Section 4", 200
