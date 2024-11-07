from __future__ import annotations
from typing import Literal


# List of scopes from https://developers.google.com/identity/protocols/oauth2/scopes
OAuth2Scope = Literal[
    # ----------------------------------------------------------------------------------
    # Gmail API, v1
    # ----------------------------------------------------------------------------------
    "https://mail.google.com/",  # Read, compose, send, and permanently delete all your email from Gmail
    "https://www.googleapis.com/auth/gmail.addons.current.action.compose",  # Manage drafts and send emails when you interact with the add-on
    "https://www.googleapis.com/auth/gmail.addons.current.message.action",  # View your email messages when you interact with the add-on
    "https://www.googleapis.com/auth/gmail.addons.current.message.metadata",  # View your email message metadata when the add-on is running
    "https://www.googleapis.com/auth/gmail.addons.current.message.readonly",  # View your email messages when the add-on is running
    "https://www.googleapis.com/auth/gmail.compose",  # Manage drafts and send emails
    "https://www.googleapis.com/auth/gmail.insert",  # Add emails into your Gmail mailbox
    "https://www.googleapis.com/auth/gmail.labels",  # See and edit your email labels
    "https://www.googleapis.com/auth/gmail.metadata",  # View your email message metadata such as labels/headers, but not the email body
    "https://www.googleapis.com/auth/gmail.modify",  # Read, compose, and send emails from your Gmail account
    "https://www.googleapis.com/auth/gmail.readonly",  # View your email messages and settings
    "https://www.googleapis.com/auth/gmail.send",  # Send email on your behalf
    "https://www.googleapis.com/auth/gmail.settings.basic",  # See, edit, create, or change your email settings and filters in Gmail
    "https://www.googleapis.com/auth/gmail.settings.sharing",  # Manage your sensitive mail settings, including who can manage your mail
    #
    # ----------------------------------------------------------------------------------
    # Google Docs API, v1
    # ----------------------------------------------------------------------------------
    "https://www.googleapis.com/auth/documents",  # See, edit, create, and delete all your Google Docs documents
    "https://www.googleapis.com/auth/documents.readonly",  # See all your Google Docs documents
    "https://www.googleapis.com/auth/drive",  # See, edit, create, and delete all of your Google Drive files
    "https://www.googleapis.com/auth/drive.file",  # See, edit, create, and delete only the specific Google Drive files you use with this app
    "https://www.googleapis.com/auth/drive.readonly",  # See and download all your Google Drive files
    #
    # ----------------------------------------------------------------------------------
    # Google Drive API, v3
    # ----------------------------------------------------------------------------------
    "https://www.googleapis.com/auth/drive",  # See, edit, create, and delete all of your Google Drive files
    "https://www.googleapis.com/auth/drive.appdata",  # See, create, and delete its own configuration data in your Google Drive
    "https://www.googleapis.com/auth/drive.file",  # See, edit, create, and delete only the specific Google Drive files you use with this app
    "https://www.googleapis.com/auth/drive.metadata",  # View and manage metadata of files in your Google Drive
    "https://www.googleapis.com/auth/drive.metadata.readonly",  # See information about your Google Drive files
    "https://www.googleapis.com/auth/drive.photos.readonly",  # View the photos, videos and albums in your Google Photos
    "https://www.googleapis.com/auth/drive.readonly",  # See and download all your Google Drive files
    "https://www.googleapis.com/auth/drive.scripts",  # Modify your Google Apps Script scripts' behavior
    #
    # ----------------------------------------------------------------------------------
    # Google Sheets API, v4
    # ----------------------------------------------------------------------------------
    "https://www.googleapis.com/auth/drive",  # See, edit, create, and delete all of your Google Drive files
    "https://www.googleapis.com/auth/drive.file",  # See, edit, create, and delete only the specific Google Drive files you use with this app
    "https://www.googleapis.com/auth/drive.readonly",  # See and download all your Google Drive files
    "https://www.googleapis.com/auth/spreadsheets",  # See, edit, create, and delete all your Google Sheets spreadsheets
    "https://www.googleapis.com/auth/spreadsheets.readonly",  # See all your Google Sheets spreadsheets
]

DOCS_RO: OAuth2Scope = "https://www.googleapis.com/auth/documents.readonly"
DOCS_RW: OAuth2Scope = "https://www.googleapis.com/auth/documents"
DRIVE_RO: OAuth2Scope = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_RW: OAuth2Scope = "https://www.googleapis.com/auth/drive"
SHEETS_RO: OAuth2Scope = "https://www.googleapis.com/auth/spreadsheets.readonly"
SHEETS_RW: OAuth2Scope = "https://www.googleapis.com/auth/spreadsheets"
