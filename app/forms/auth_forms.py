from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Regexp


class LoginForm(FlaskForm):
    username = StringField(
        "Username or Email",
        validators=[DataRequired(), Length(max=255)],
        render_kw={"placeholder": "username or email", "autofocus": True,
                   "autocomplete": "username"}
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired()],
        render_kw={"placeholder": "password", "autocomplete": "current-password"}
    )
    remember_me = BooleanField("Keep me signed in")


class ForgotPasswordForm(FlaskForm):
    email = StringField(
        "Your Email Address",
        validators=[DataRequired(), Email(), Length(max=255)],
        render_kw={"placeholder": "you@company.com", "autofocus": True}
    )


class ResetPasswordForm(FlaskForm):
    new_password = PasswordField(
        "New Password",
        validators=[
            DataRequired(),
            Length(min=12, max=128),
            Regexp(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*#?&^_\-])',
                   message="Must include uppercase, lowercase, digit and special character.")
        ]
    )
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")]
    )


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Current Password",
        validators=[DataRequired()]
    )
    new_password = PasswordField(
        "New Password",
        validators=[
            DataRequired(),
            Length(min=12, max=128),
            Regexp(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*#?&^_\-])',
                   message="Must include uppercase, lowercase, digit and special character.")
        ]
    )
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")]
    )
