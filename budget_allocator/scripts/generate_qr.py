import sys
import os

try:
    import qrcode
except ImportError:
    print("\n[!] Error: 'qrcode' library not found.")
    print("Please install it for testing using:")
    print("    pip install qrcode[pil]\n")
    sys.exit(1)

def generate_qr(uri: str, output_file: str = "mfa_qr.png"):
    """Generates a QR code image from a TOTP URI."""
    print(f"Generating QR code for URI: {uri}")
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    
    # Save the file in the current directory
    abs_path = os.path.abspath(output_file)
    img.save(abs_path)
    
    print("\n" + "="*50)
    print("  SUCCESS!")
    print("="*50)
    print(f"  QR Code saved to: {abs_path}")
    print("  Scan this image with your Authenticator app.")
    print("="*50 + "\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/generate_qr.py <TOTP_URI>")
        print("Example: python scripts/generate_qr.py \"otpauth://totp/BudgetApp:user@example.com?secret=JBSWY3DPEHPK3PXP&issuer=BudgetApp\"")
        sys.exit(1)
    
    uri_input = sys.argv[1]
    generate_qr(uri_input)
