"""
Flask web application created to help researchers for the PALP project connect different sources of data to each other.
[Github repository](https://github.com/p-lod/PALP-Workspace)
"""
from __future__ import print_function
from flask import Flask, render_template, session, json, request, redirect, flash
from flask_mysqldb import MySQL
from google.cloud import translate_v2 as translate
from google.oauth2 import service_account
from googleapiclient.discovery import build
import boxsdk
import json
import re
from datetime import datetime
import os
import glob
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from markupsafe import escape
import requests
from types import SimpleNamespace
from langdetect import detect
from wikidata.client import Client


# === Setup and Authentication ===

# Using [Sentry](https://sentry.io/) to log and report errors
sentry_sdk.init(
    dsn="https://5ebc9319ed40454993186c71e8c35553@o493026.ingest.sentry.io/5561383",
    integrations=[FlaskIntegration()],
    traces_sample_rate=1.0
)

# Set up Flask
app = Flask(__name__)
client = Client()
app.config["SECRET_KEY"] = "ShuJAxtrE8tO5ZT"

# MySQL configurations
with open('mysql.cfg', 'r') as mysql_cfg:
    mysql_cfg_lines = mysql_cfg.read().splitlines()
    app.config['MYSQL_USER'] = mysql_cfg_lines[0]
    app.config['MYSQL_PASSWORD'] = mysql_cfg_lines[1]
    app.config['MYSQL_DB'] = mysql_cfg_lines[2]
    app.config['MYSQL_HOST'] = mysql_cfg_lines[3]
mysql = MySQL(app)

#[Google Translate](https://cloud.google.com/translate/docs) and [Google Sheets](https://developers.google.com/sheets/api) credentials
tr_credentials = service_account.Credentials.from_service_account_file("My Project-1f2512d178cb.json")
translate_client = translate.Client(credentials=tr_credentials)

scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
scoped_gs = tr_credentials.with_scopes(scopes)
sheets_client = build('sheets', 'v4', credentials=scoped_gs)
sheet = sheets_client.spreadsheets()
tracking_ws = "1F4nXX1QoyV1miaRUop2ctm8snDyov6GNu9aLt9t3a3M"
ranges = "Workflow_Tracking!A3:S87078"

drive_client = build('drive', 'v3', credentials=scoped_gs)

#[Box API](https://developer.box.com/) configurations
with open('box_config.json', 'r') as f:
    boxapi = json.load(f)
box_auth = boxsdk.JWTAuth(
    client_id=boxapi["boxAppSettings"]["clientID"],
    client_secret=boxapi["boxAppSettings"]["clientSecret"],
    enterprise_id=boxapi["enterpriseID"],
    jwt_key_id=boxapi["boxAppSettings"]["appAuth"]["publicKeyID"],
    rsa_private_key_data=boxapi["boxAppSettings"]["appAuth"]["privateKey"],
    rsa_private_key_passphrase=boxapi["boxAppSettings"]["appAuth"]["passphrase"],
)

box_access_token = box_auth.authenticate_instance()
box_client = boxsdk.Client(box_auth)

# === Helper Functions ===

# Roman numeral utility. Takes in integer Arabic number to be converted
# (must be between 1 and 9) and turns it into a string of the Roman numeral.
def toRoman(data):
    romans = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"]
    if data.isnumeric():
        romin = int(data) - 1
    else:
        romin = 0
    if romin >= 0 and romin < len(romans):
        romreg = romans[romin]
    else:
        romreg = data
    return romreg

# Main function to retrieve information from various data sources.
# Called often at beginning of page load to make sure all information is up to date.
def pullPre():
    gsheet = sheet.values().get(spreadsheetId=tracking_ws, range=ranges, majorDimension="COLUMNS").execute()
    values = gsheet.get('values', [])
    links = values[11]
    dones = values[18]
    artsDW = values[12]

    for a, v in session['ARClist'].items():
        is_art = "no"
        is_plaster = "no"
        v["pinpimgs"] = []
        v["ppmimgs"] = []
        pinpCur = mysql.connection.cursor()
        pinpQuery = "SELECT * FROM `PinP_preq` WHERE `ARC`='" + a + "' OR `other_ARC` LIKE '%" + a + "%';"
        pinpCur.execute(pinpQuery)
        pinpdata = pinpCur.fetchall()
        pinpCur.close()
        for d in pinpdata:
            v["pinpimgs"].append(d[0])
            if d[1] == "maybe" and is_art == "no":
                is_art = "maybe"
            if d[1] == "yes" and (is_art == "no" or is_art == "maybe"):
                is_art = "yes"
            if d[2] == "maybe" and is_plaster == "no":
                is_plaster = "maybe"
            if d[2] == "yes" and (is_plaster == "no" or is_plaster == "maybe"):
                is_plaster = "yes"
            v["notes"] += d[5]

        ppmCur = mysql.connection.cursor()
        ppmQuery = "SELECT * FROM `PPM_preq` WHERE `ARC`='" + a + "' OR `other_ARC` LIKE '%" + a + "%';"
        ppmCur.execute(ppmQuery)
        ppmdata = ppmCur.fetchall()
        ppmCur.close()
        for d in ppmdata:
            v["ppmimgs"].append(d[0])
            if d[1] == "maybe" and is_art == "no":
                is_art = "maybe"
            if d[1] == "yes" and (is_art == "no" or is_art == "maybe"):
                is_art = "yes"
            if d[2] == "maybe" and is_plaster == "no":
                is_plaster = "maybe"
            if d[2] == "yes" and (is_plaster == "no" or is_plaster == "maybe"):
                is_plaster = "yes"
            v["notes"] += d[5]

        v["is_art"] = is_art
        v["is_plaster"] = is_plaster

        l = v["trackerindex"]
        if links[l]:
            v["link"] = links[l]
        if dones[l]:
            v["done"] = True
        if "No from DW" in artsDW[l]:
            v["noart"] = True
        if "Unknown from DW" in artsDW[l]:
            v["unknown"] = True

        arcCur = mysql.connection.cursor()
        arcQuery = 'SELECT uuid FROM PPP_desc WHERE ARCs LIKE "%' + a + '%";'
        arcCur.execute(arcQuery)
        newarcs = arcCur.fetchall()
        v['ppps'] = []
        if len(newarcs) > 0:
            for n in newarcs:
                v['ppps'].append(n[0])

@app.route("/search", methods=['GET'])
def search():
    search_term = request.args.get("search")
    search_data = getSearchTerms()
    syns = getAllSynonyms()
    term_desc = ""
    wiki_desc = ""
    getty = "-"
    wikiID = "-"
    imgUrl = ""
    otherImgUrl = ""
    for syn in syns:
        if search_term == syn:
            search_term = getTermForSyn(syn, search_data)
    if search_term == "term not found":
        return render_template('index.html', arc="", error=search_term)
    else:
        for key in search_data:
            if search_term == key:
                getty = search_data[key]["getty"]
                wikiID = search_data[key]["wiki"]
                otherImgUrl = search_data[key]["otherImageUrl"]
                if getty != "-":
                    try:
                        response = requests.get(getty.replace("page/", "", -1) + ".json")
                        data = json.loads(response.content)
                        value = ""
                        for binding in data["results"]["bindings"]:
                            binding_value = binding["Subject"]["value"]
                            if binding_value.startswith("http://vocab.getty.edu/aat/scopeNote/"):
                                noteData = json.loads(requests.get(binding_value + ".json").content)
                                desc = noteData[binding_value]["http://www.w3.org/1999/02/22-rdf-syntax-ns#value"][0]["value"]
                                if value == "":
                                    if detect(desc) == 'en':
                                        value = desc
                        term_desc = value
                    except Exception as err:
                        print(f'Error: {err}')
                if wikiID != "-":
                    try:
                        entity = client.get(wikiID, load=True)
                        wiki_desc = entity.description
                        image_prop = client.get('P18')
                        image = entity[image_prop]
                        imgUrl = image.image_url
                    except Exception as err:
                        imgUrl = ""
                        print(f'Error: {err}')
    synsForTerm = search_data[search_term]['synonyms'].split(",")
    num_syns = len(synsForTerm)
    if synsForTerm == [""]:
        num_syns = 0
        synsForTerm = []
    return render_template("/search_result.html", num_syns=num_syns, synonyms=synsForTerm, term=search_term, gettyUrl=getty, wikiUrl=('https://www.wikidata.org/wiki/' + wikiID), description=term_desc, wikiData=wiki_desc, img_url=imgUrl, other_img_url=otherImgUrl, other_desc=search_data[search_term]['otherDesc'], other_url=search_data[search_term]['otherUrl'])

def getSearchTerms():
    search_doc_id = "1kzWEh3v5sEJYARxYVikHdinfKIxbstYhP4HjFTh_dw4"
    res = sheet.values().get(spreadsheetId=search_doc_id, range="Old Vocabulary (v4) with links!A2:H").execute()
    terms = {}
    for row in res['values']:
        terms[row[0]] = {'getty': row[2].replace("\"", "", -1), 'wiki': row[1], 'otherDesc': row[3], 'otherImageUrl': row[4], 'otherUrl': row[5], 'synonyms': row[6], 'category': row[7]}
    return terms

def getTermForSyn(syn, terms):
    for key in terms:
        syns = terms[key]['synonyms']
        if syns != "":
            if "," in syns:
                for synonym in syns.split(","):
                    if synonym == syn:
                        return key
            else:
                if syns == syn:
                    return key
    return "term not found"

def getAllTerms():
    search_doc_id = "1kzWEh3v5sEJYARxYVikHdinfKIxbstYhP4HjFTh_dw4"
    res = sheet.values().get(spreadsheetId=search_doc_id, range="Old Vocabulary (v4) with links!A2:H").execute()
    terms = {}
    for row in res['values']:
        terms[row[0]] = {'getty': row[2].replace("\"", "", -1), 'wiki': row[1], 'otherDesc': row[3], 'otherImageUrl': row[4], 'otherUrl': row[5], 'synonyms': row[6], 'category': row[7]}
    search_d = terms
    all_terms = []
    for term in search_d:
        all_terms.append(term)
        synString = search_d[term]['synonyms']
        if synString != "":
            if "," in synString:
                synonyms = synString.split(",")
                for syn in synonyms:
                    all_terms.append(syn)
            else:
                all_terms.append(synString)
    return all_terms

def getAllSynonyms():
    search_doc_id = "1kzWEh3v5sEJYARxYVikHdinfKIxbstYhP4HjFTh_dw4"
    res = sheet.values().get(spreadsheetId=search_doc_id, range="Old Vocabulary (v4) with links!A2:H").execute()
    terms = {}
    for row in res['values']:
        terms[row[0]] = {'getty': row[2].replace("\"", "", -1), 'wiki': row[1], 'otherDesc': row[3], 'otherImageUrl': row[4], 'otherUrl': row[5], 'synonyms': row[6], 'category': row[7]}
    search_d = terms
    syns = []
    for term in search_d:
        synString = search_d[term]['synonyms']
        if synString != "":
            if "," in synString:
                synonyms = synString.split(",")
                for syn in synonyms:
                    syns.append(syn)
            else:
                syns.append(synString)
    return syns

# === Forms ===

# Log-in form. Pulls credentials from user.cfg
@app.route("/login", methods=['POST'])
def login():
    error = ""
    with open('user.cfg', 'r') as user_cfg:
        user_lines = user_cfg.read().splitlines()
        username = user_lines[0]
        password = user_lines[1]
    if request.form['password'] == password and request.form['username'] == username:
        session['logged_in'] = True
    else:
        error = 'Sorry, wrong password!'
    return render_template('index.html', error=error)

# Form submitted from home page to select location
@app.route('/init', methods=['POST'])  # Form submitted from home page
def init():
    session['carryoverPPP'] = ""
    session['carryoverPPPids'] = []
    session['region'] = ""
    session['insula'] = ""
    session['property'] = ""
    session['room'] = ""

    if request.form.get('region'):
        if request.form['region']:
            session['region'] = request.form['region']

    if request.form.get('insula'):
        if request.form['insula']:
            session['insula'] = request.form['insula']
        else:
            session['insula'] = ""

    if request.form.get('property'):
        if request.form['property']:
            session['property'] = request.form['property']
        else:
            session['property'] = ""

    if request.form.get('room'):
        if request.form['room']:
            session['room'] = request.form['room']
        else:
            session['room'] = ""

    building = "r" + str(session['region']) + "-i"+str(session['insula']) + "-p" + session['property'] + "-space-" + session['room']
    session['ARClist'] = {}
    session['current'] = ""

    gsheet = sheet.values().get(spreadsheetId=tracking_ws, range=ranges, majorDimension="COLUMNS").execute()
    values = gsheet.get('values', [])
    locationlist = values[1]
    arclist = values[7]

    # Set up dictionary for information about each ARC at location
    for l in range(len(locationlist)):
        places = locationlist[l].split("-")
        if (places[0] == "r" + str(session['region'])) and ((places[1] == "i" + str(session['insula'])) or session['insula'] == "") and ((places[2] == "p" + str(session['property'])) or session['property'] == "") and (("-".join(places[3:]) == "space-" + str(session['room'])) or session['room'] == ""):
            session['ARClist'][arclist[l]] = {"link": "None",
                                              "is_art": "Not defined",
                                              "is_plaster": "Not defined",
                                              "pinpimgs": [],
                                              "ppmimgs": [],
                                              "notes": "",
                                              "done": False,
                                              "noart": False,
                                              "unknown": False,
                                              "trackerindex": l,
                                              "ppps": []}

    return redirect('/ARCs')

# === User Pages ===

# Custom server error handler
@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

# Home page, displays either login form or location choice form
@app.route("/")
def index():
    return render_template('index.html')

# Initial call to [pullPre()](#helper-functions) to get ARC options
@app.route('/ARCs')
def chooseARCs():
    if session.get('logged_in') and session["logged_in"]:
        pullPre()
        return render_template("chooseARCs.html", arcs=session['ARClist'],
                               region=session['region'], insula=session['insula'],
                               property=session['property'], room=session['room'], allTerms=getAllTerms())
    else:
        error = "Sorry, this page is only accessible by logging in."
        return render_template('index.html', arc="", error=error)

# Clear old variables and set session ARC
@app.route('/makedoc/<chosenarc>')
def makedoc(chosenarc):
    session['carryoverPPP'] = ""
    session['carryoverPPPids'] = []
    session['current'] = chosenarc

    return redirect('/PPP')

# Display PPP entries and allow the user to select which ones belong to the current ARC
@app.route("/PPP")
def showPPP():

    if session.get('logged_in') and session["logged_in"]:
        pullPre()
        inswithz = propwithz = ""

        pppCur = mysql.connection.cursor()
        rm = ""
        # User may not have input room identifier
        if session['room']:
            rm = "' and `Room` = '" + session['room']
        #Get related PPP entries for location
        pppQuery = "SELECT uuid, description, id, location, material, Room FROM PPP WHERE `Region` = '" + session['region'] + "' and `Insula` = '" + session['insula'] + "' and `Doorway` = '" + session['property'] + rm+"';"

        pppCur.execute(pppQuery)
        data = pppCur.fetchall()
        pppCur.close()

        indices = []
        for d in data:
            indices.append(d[0])

        transdata = []
        dataplustrans = []
        for d in data:
            # PPP data is written in Italian; use live Google Translate
            translation = translate_client.translate(d[1], target_language="en", source_language="it")
            transdata.append(translation['translatedText'])
            dlist = list(d)
            dlist.append(translation['translatedText'])

            arcCur = mysql.connection.cursor()
            arcQuery = 'SELECT ARCs FROM PPP_desc WHERE uuid = "' + d[0] + '";'
            arcCur.execute(arcQuery)
            newarcs = arcCur.fetchall()
            arcCur.close()
            if len(newarcs) > 0:
                dlist.append(newarcs[0][0])
            else:
                dlist.append("")

            dataplustrans.append(dlist)

        current = session['current']
        v = session['ARClist'][current]
        carryCur = mysql.connection.cursor()
        # Show descriptions from entries previously selected as related to this ARC
        carryQuery = "SELECT description, reviewed FROM PPP WHERE uuid in ('" + "','".join(v["ppps"]) + "') ;"
        carryCur.execute(carryQuery)
        dataList = carryCur.fetchall()
        carryCur.close()

        dataCopy = ""
        for d in dataList:
            dataCopy += translate_client.translate(d[0], target_language="en", source_language="it")['translatedText'] + "; "

        session['carryoverPPP'] = dataCopy

        return render_template('PPP.html',
                               catextppp=session['carryoverPPP'], dbdata=dataplustrans, indices=indices, arc=session['current'],
                               region=session['region'], insula=session['insula'], property=session['property'], room=session['room'], allTerms=getAllTerms())

    else:
        error = "Sorry, this page is only accessible by logging in."
        return render_template('index.html', arc="", error=error)

# Show PPM and PinP images tagged with this ARC from the [Prequel Workspace](https://prequel.p-lod.umasscreate.net/)
@app.route("/associated")
def showAssociated():

    if session.get('logged_in') and session["logged_in"]:
        pullPre()
        base_url = "http://umassamherst.lunaimaging.com/luna/servlet/as/search?"
        current = session['current']
        d = session['ARClist'][current]
        totpinp = []
        for p in d['pinpimgs']:
            thispinp = []
            assocCur = mysql.connection.cursor()
            assocQuery = "SELECT DISTINCT `archive_id`, `img_alt` FROM `PinP` WHERE `archive_id` = '"+str(p)+"' ORDER BY `img_url` "
            assocCur.execute(assocQuery)
            all0 = assocCur.fetchall()
            for p in all0[0]:
                thispinp.append(p)
            params = ["q=filename=image"+str(all0[0][0])+ ".jpg", "lc=umass~14~14"]
            request = requests.get(base_url+"&".join(params))
            result = request.json()
            if len(result['results']) > 0:
                thispinp.append(result['results'][0]['urlSize1'])
                thispinp.append(result['results'][0]['id'])
            else:
                thispinp.append("")
                thispinp.append("")
            assocCur.close()
            totpinp.append(thispinp)
        totppm = []
        for p in d['ppmimgs']:
            thisppm = []
            assocCur = mysql.connection.cursor()
            assocQuery = "SELECT DISTINCT `id`, `translated_text` FROM `PPM` WHERE `id` = '"+str(p)+"'"
            assocCur.execute(assocQuery)
            all0 = assocCur.fetchall()
            for p in all0[0]:
                thisppm.append(p)
            ### START BOX - to be replaced
            itemid = "0"
            searchid = "\"" + all0[0][0] + ".jpg\""
            box_id = box_client.search().query(query=searchid, file_extensions=['jpg'], ancestor_folder_ids="138198238999", fields=["id", "name"], content_types=["name"])
            for item in box_id:
                if item.name == all0[0][0] + ".jpg":
                    itemid = item.id
                    break
            filename = str(itemid) + ".jpg"
            if not os.path.exists("static/images/"+filename):
                try:
                    thumbnail = box_client.file(itemid).get_thumbnail(extension='jpg', min_width=200)
                except boxsdk.BoxAPIException as exception:
                    thumbnail = bytes(exception.message, 'utf-8')
                with open(os.path.join("static/images", filename), "wb") as file:
                    file.write(thumbnail)
            assocCur.close()
            thisppm.append("https://app.box.com/file/"+str(itemid))
            thisppm.append("static/images/"+filename)
            ### END BOX
            # params = ["q=filename=image"+str(all0[0][0])+ ".jpg", "lc=umass~14~14"]
            # request = requests.get(base_url+"&".join(params))
            # result = request.json()
            # if len(result['results']) > 0:
            #     thisppm.append(result['results'][0]['urlSize1'])
            #     thisppm.append(result['results'][0]['id'])
            # else:
            #     thisppm.append("")
            #     thisppm.append("")
            assocCur.close()
            totppm.append(thisppm)
        return render_template('associated.html', arc=session['current'],
                               region=session['region'], insula=session['insula'], property=session['property'], room=session['room'],
                               totpinp=totpinp, totppm=totppm, allTerms=getAllTerms())

    else:
        error = "Sorry, this page is only accessible by logging in."
        return render_template('index.html', arc="", error=error)

# Assist user in copying data from workspace site to Google Sheet
@app.route('/descriptions')
def showDescs():
    if session.get('logged_in') and session["logged_in"]:
        pullPre()

        current = session['current']
        gdoc = session['ARClist'][current]['link']

        # Copy template spreadsheet if one doesn't exist yet for this ARC
        if 'http' not in gdoc:
            template_spreadsheet_id = "13M3sk4RAOy2Jlq86ECdwR8m11MsOaUNF1unbP6yQF-g"
            request_body = {"name": "Workspace_5_" + current, "parents": ['1G_ZH-20qmxudaymDXMPe0wT4w_C_r00Q']}
            response = drive_client.files().copy(fileId=template_spreadsheet_id, body=request_body, supportsAllDrives=True).execute()
            newID = response['id']

            #Update [Workflow Tracker](https://docs.google.com/spreadsheets/d/1F4nXX1QoyV1miaRUop2ctm8snDyov6GNu9aLt9t3a3M/edit) with new URL (this also marks it as "working")
            newrange = "Workflow_Tracking!L" + str(session['ARClist'][current]['trackerindex']+3)
            new_request = {"values": [["https://docs.google.com/spreadsheets/d/" + newID]]}
            updatelink = sheet.values().update(spreadsheetId=tracking_ws, range=newrange, body=new_request, valueInputOption="USER_ENTERED").execute()

            session['ARClist'][current]['link'] = "https://docs.google.com/spreadsheets/d/" + newID

            # Users allowed to access the description sheet
            auth_users = ['smastroianni@umass.edu', 'fdipietro@umass.edu', 'bmai@umass.edu', 'nicmjohnson@umass.edu', 'mcknapp@umass.edu',
                          'dbeason@umass.edu', 'lfield@umass.edu', 'tbernard@umass.edu', 'mhoffenberg@umass.edu', 'gsharaga@umass.edu', 'droller@umass.edu',
                          'shazizi@umass.edu', 'laurejt@umass.edu', 'abrenon@umass.edu', 'epoehler@classics.umass.edu', 'epoehler@gmail.com',
                          'palp-workspace@my-project-1537454316408.iam.gserviceaccount.com', 'plod@umass.edu', 'plodAD97@gmail.com']
            for u in auth_users:
                drive_client.permissions().create(body={"role": "writer", "type": "user", 'emailAddress': u, 'sendNotificationEmail': False}, fileId=newID).execute()
            drive_client.permissions().create(body={"role": "owner", "type": "user", "emailAddress": "plodAD79@gmail.com"}, transferOwnership=True, fileId=newID).execute()

        gdoc = session['ARClist'][current]['link']
        d = session['ARClist'][current]

        # Show chosen PinP image descriptions
        totpinp = []
        for p in d['pinpimgs']:
            assocCur = mysql.connection.cursor()
            assocQuery = "SELECT DISTINCT `img_alt` FROM `PinP` WHERE `archive_id` = '"+str(p)+"' ORDER BY `img_url` "
            assocCur.execute(assocQuery)
            all0 = assocCur.fetchall()
            for a in all0:
                totpinp.append(a[0])

        # Show chosen PPM image descriptions
        totppm = []
        for p in d['ppmimgs']:
            assocCur = mysql.connection.cursor()
            assocQuery = "SELECT DISTINCT `translated_text` FROM `PPM` WHERE `id` = '"+str(p)+"'"
            assocCur.execute(assocQuery)
            all0 = assocCur.fetchall()
            for a in all0:
                totppm.append(a[0])

        carryoverpinp = "; ".join(totpinp)
        carryoverppm = "; ".join(totppm)

        # Get chosen PPP entries and translate
        current = session['current']
        v = session['ARClist'][current]
        carryCur = mysql.connection.cursor()
        carryQuery = "SELECT description, reviewed FROM PPP WHERE uuid in ('" + "','".join(v["ppps"]) + "') ;"
        carryCur.execute(carryQuery)
        dataList = carryCur.fetchall()
        carryCur.close()

        dataCopy = ""
        for d in dataList:
            dataCopy += translate_client.translate(d[0], target_language="en", source_language="it")['translatedText'] + "; "

        session['carryoverPPP'] = dataCopy

        return render_template('descs.html',
                               carryoverPPP=session['carryoverPPP'], carryoverPPM=carryoverppm, carryoverPinP=carryoverpinp,
                               region=session['region'], insula=session['insula'], property=session['property'], room=session['room'], gdoc=gdoc,
                               arc=current, allTerms=getAllTerms())
    else:
        error = "Sorry, this page is only accessible by logging in."
        return render_template('index.html', arc="", error=error)

# Help page
@app.route('/help')
def helppage():
    return render_template('help.html', allTerms=getAllTerms())

# === Update forms ===

#When PPP items are changed via update form, update database
@app.route('/update-ppp', methods=['POST'])
def updatePPP():
    pppCur = mysql.connection.cursor()
    dictargs = request.form.to_dict()
    date = datetime.now().strftime("%Y-%m-%d")
    for k, v in dictargs.items():
        vrep = v.replace('\n', ' ').replace('\r', ' ').replace('\'', "\\'")
        vrep = escape(vrep)
        sep = k.split("_")
        pppQuery = "INSERT INTO PPP(`uuid`) SELECT * FROM ( SELECT '" + sep[0] + "' ) AS tmp WHERE NOT EXISTS ( SELECT 1 FROM PPP WHERE `uuid` = '" + sep[0] + "' ) LIMIT 1;"
        pppCur.execute(pppQuery)
        mysql.connection.commit()
        if sep[1] == "a":
            pppQueryA = "UPDATE PPP SET `id` = '" + vrep + "' WHERE `uuid` = '" + sep[0] + "';"
            pppCur.execute(pppQueryA)
        if sep[1] == "b":
            pppQueryB = "UPDATE PPP SET `location` = '" + vrep + "' WHERE `uuid` = '" + sep[0] + "';"
            pppCur.execute(pppQueryB)
        if sep[1] == "c":
            pppQueryC = "UPDATE PPP SET `material` = '" + vrep + "' WHERE `uuid` = '" + sep[0] + "';"
            pppCur.execute(pppQueryC)
        if sep[1] == "d":
            pppQueryD = "UPDATE PPP SET `description` = '" + vrep + "' WHERE `uuid` = '" + sep[0] + "';"
            pppCur.execute(pppQueryD)
        if sep[1] == "e":
            pppQueryE = "UPDATE PPP SET `Region` = '" + vrep + "' WHERE `uuid` = '" + sep[0] + "';"
            pppCur.execute(pppQueryE)
        if sep[1] == "f":
            pppQueryF = "UPDATE PPP SET `Insula` = '" + vrep + "' WHERE `uuid` = '" + sep[0] + "';"
            pppCur.execute(pppQueryF)
        if sep[1] == "g":
            pppQueryG = "UPDATE PPP SET `Doorway` = '" + vrep + "' WHERE `uuid` = '" + sep[0] + "';"
            pppCur.execute(pppQueryG)
        if sep[1] == "h":
            pppQueryH = "UPDATE PPP SET `Room` = '" + vrep + "' WHERE `uuid` = '" + sep[0] + "';"
            pppCur.execute(pppQueryH)
        if sep[1] == "i":
            pppQueryI = 'INSERT INTO `PPP_desc` (uuid, ARCs, date_added) VALUES ("' + sep[0]+'","'+vrep+'","'+date+'") ON DUPLICATE KEY UPDATE `ARCs` = "' + vrep + '", `date_added` = "' + date + '";'
            pppCur.execute(pppQueryI)
    mysql.connection.commit()
    pppCur.close()

    return redirect('/PPP')

# Save associated PPP text to help with description
@app.route('/carryover-button')
def carryover_button():
    if request.args.get('catextppp'):

        strargs = request.args['catextppp'].replace("[", "").replace("]", "")
        date = datetime.now().strftime("%Y-%m-%d")
        for i in strargs.split(","):
            addCur = mysql.connection.cursor()
            # If there's already an ARC marked related to this entry, replace it with new choice
            addQuery = 'INSERT INTO `PPP_desc` (uuid, ARCs, date_added) VALUES (' + i + ',"'+session["current"]+'","' + date + '") ON DUPLICATE KEY UPDATE `ARCs` = "' + session["current"] + '", `date_added` = "' + date + '";'
            addCur.execute(addQuery)
            addCur.close()

        current = session['current']
        v = session['ARClist'][current]
        arcCur = mysql.connection.cursor()
        arcQuery = 'SELECT uuid FROM PPP_desc WHERE ARCs LIKE "%' + current + '%";'
        arcCur.execute(arcQuery)
        newarcs = arcCur.fetchall()
        v['ppps'] = []
        if len(newarcs) > 0:
            for n in newarcs:
                v['ppps'].append(n[0])

        return redirect("/associated")
    else:
        return redirect("/PPP")

# === Update [Workspace Tracker Google Sheet](https://docs.google.com/spreadsheets/d/1F4nXX1QoyV1miaRUop2ctm8snDyov6GNu9aLt9t3a3M/edit) ===

#Update the tracker "art" column to say "No from DW"
@app.route('/noart')
def noart():
    chosenarc = session['current']
    newrange = "Workflow_Tracking!M" + str(session['ARClist'][chosenarc]['trackerindex']+3)
    new_request = {"values": [["No from DW"]]}
    updatelink = sheet.values().update(spreadsheetId=tracking_ws, range=newrange, body=new_request, valueInputOption="RAW").execute()

    return redirect('/ARCs')

# Update the tracker "art" column to say "Unknown from DW"
@app.route('/unknownart')
def unknownart():
    chosenarc = session['current']
    newrange = "Workflow_Tracking!M" + str(session['ARClist'][chosenarc]['trackerindex']+3)
    new_request = {"values": [["Unknown from DW"]]}
    updatelink = sheet.values().update(spreadsheetId=tracking_ws, range=newrange, body=new_request, valueInputOption="RAW").execute()

    return redirect('/ARCs')

# Update the tracker when user marks "Done"
@app.route('/done', methods=['POST', 'GET'])
def done():
    # Hero image comes either from PPM or PinP; db query is different depending on this
    db = request.form['pinporppm']
    imgid = request.form.get("hero")
    dbid = ""
    if db == "PinP_preq":
        dbid = "archive_id"
    if db == "PPM_preq":
        dbid = "id"
    date = datetime.now().strftime("%Y-%m-%d")
    ppmCur = mysql.connection.cursor()
    ppmQuery = 'INSERT INTO `'+db+'` ('+dbid+', hero_image, date_added) VALUES (' + imgid + ',"1",' + date + ') ON DUPLICATE KEY UPDATE `hero_image` = "1", `date_added` = "' + date + '";'
    ppmCur.execute(ppmQuery)
    mysql.connection.commit()
    ppmCur.close()

    chosenarc = session['current']
    newrange = "Workflow_Tracking!S" + str(session['ARClist'][chosenarc]['trackerindex']+3)
    new_request = {"values": [[datetime.now().strftime("%m/%d/%Y")]]}
    updatelink = sheet.values().update(spreadsheetId=tracking_ws, range=newrange, body=new_request, valueInputOption="RAW").execute()

    return redirect("/ARCs")

# === Separate PPP single item update page ===

# Different credentials from main site
@app.route("/PPP-login", methods=['POST'])
def PPPlogin():
    error = ""
    with open('userPPP.cfg', 'r') as user_cfg:
        user_lines = user_cfg.read().splitlines()
        username = user_lines[0]
        password = user_lines[1]
    if request.form['password'] == password and request.form['username'] == username:
        session['PPPlogged_in'] = True
        return redirect('/PPP-single')
    else:
        error = 'Sorry, wrong password!'
        return render_template('PPP-single.html', dbdata="", error=error)

# Edit one PPP at a time. URL parameter can be PPP ID or UUID
@app.route("/PPP-single")
def showPPPSingle():
    if session.get('PPPlogged_in') and session["PPPlogged_in"]:

        error = ""
        pppCur = mysql.connection.cursor()
        if request.args.get('uuid'):
            pppQuery = "SELECT uuid, id, location, material, description, condition_ppp, style, bibliography, photo_negative FROM PPP WHERE `uuid` = '"+str(request.args['uuid'])+"';"
            try:
                pppCur.execute(pppQuery)
                data = pppCur.fetchall()
            except Exception as exception:
                data = 'error'
                error = "You searched for Unique ID "+request.args['uuid']+". That doesn't exist - please add an entry or try again."
            if len(data) < 1:
                data = 'error'
                error = "You searched for Unique ID "+request.args['uuid']+". That doesn't exist - please add an entry or try again."

        elif request.args.get('id'):
            pppQuery = "SELECT uuid, id, location, material, description, condition_ppp, style, bibliography, photo_negative FROM PPP WHERE `id` = '"+str(request.args['id'])+"';"
            try:
                pppCur.execute(pppQuery)
                data = pppCur.fetchall()
            except Exception:
                data = 'error'
                error = "You searched for PPPID "+request.args['id']+". That doesn't exist - please add an entry or try again."
            if len(data) < 1:
                data = 'error'
                error = "You searched for PPPID "+request.args['id']+". That doesn't exist - please add an entry or try again."
        else:
            data = 'error'
            error = "Please put a query in the URL using the format <a href='https://workspace.p-lod.umasscreate.net/PPP-single?id='>https://workspace.p-lod.umasscreate.net/PPP-single?id=</a> or <a href='https://workspace.p-lod.umasscreate.net/PPP-single?uuid='>https://workspace.p-lod.umasscreate.net/PPP-single?uuid=</a>."
        pppCur.close()

        return render_template('PPP-single.html', dbdata=data, error=error)

    else:
        error = "This page is only accessible by logging in."
        return render_template('PPP-single.html', dbdata="", error=error)

# Add or edit PPP entry in database
@app.route('/update-ppp-edit', methods=['POST'])
def updatePPPEdit():
    pppCur = mysql.connection.cursor()
    dictargs = request.form.to_dict()
    date = datetime.now().strftime("%Y-%m-%d")
    sep = dictargs['uuid']
    for k, v in dictargs.items():
        pppQuery = "INSERT INTO PPP(`uuid`) SELECT * FROM ( SELECT '" + sep + "' ) AS tmp WHERE NOT EXISTS ( SELECT 1 FROM PPP WHERE `uuid` = '" + sep + "' ) LIMIT 1;"
        pppCur.execute(pppQuery)
        mysql.connection.commit()
        vrep = v.replace('\n', ' ').replace('\r', ' ').replace('\'', "\\'")
        if k == "PPPID":
            pppQueryA = "UPDATE PPP SET `id` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryA)
        if k == "location":
            pppQueryB = "UPDATE PPP SET `location` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryB)
        if k == "material":
            pppQueryC = "UPDATE PPP SET `material` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryC)
        if k == "description":
            pppQueryD = "UPDATE PPP SET `description` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryD)
        if k == "condition":
            pppQueryE = "UPDATE PPP SET `condition_ppp` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryE)
        if k == "style":
            pppQueryF = "UPDATE PPP SET `style` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryF)
        if k == "bibliography":
            pppQueryG = "UPDATE PPP SET `bibliography` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryG)
        if k == "negative":
            pppQueryH = "UPDATE PPP SET `photo_negative` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryH)
        if k == "region":
            pppQueryI = "UPDATE PPP SET `Region` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryI)
        if k == "insula":
            pppQueryJ = "UPDATE PPP SET `Insula` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryJ)
        if k == "doorway":
            pppQueryK = "UPDATE PPP SET `Doorway` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryK)
        if k == "room":
            pppQueryL = "UPDATE PPP SET `Room` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryL)
        if k == "volume":
            pppQueryM = "UPDATE PPP SET `volume` = '" + vrep + "' WHERE `uuid` = '" + sep + "';"
            pppCur.execute(pppQueryM)
    mysql.connection.commit()
    pppCur.close()

    # When new entry added, redirect back to page asking you to add parameters
    if sep.startswith('new'):
        return redirect('/PPP-single')
    # Otherwise, "next" button leads user to next uuid numerically
    else:
        nextidint = int(sep) + 1
        nextid = str(nextidint)
        return redirect('/PPP-single?uuid='+nextid)

# === Separate PPM single item update page ===

@app.route("/PPM-single-search", methods=['POST', 'GET'])
def PPMSingleSearch():
    if session.get('PPPlogged_in') and session["PPPlogged_in"]:
        if request.form.get('region'):
            region = request.form['region']
        else:
            region = ""
        if request.form.get('insula'):
            insula = request.form['insula']
        else:
            insula = ""
        if request.form.get('property'):
            prop = request.form['property']
        else:
            prop = ""
        if request.form.get('room'):
            room = request.form['room']
        else:
            room = ""
        if room != "" or prop != "" or insula != "" or region != "":
            ppmCur = mysql.connection.cursor()
            ppmQuery = "SELECT id, description, image_path FROM PPM WHERE region LIKE %s AND insula LIKE %s AND doorway LIKE %s AND room LIKE %s ORDER BY `image_path` ASC;"
            loc = []
            if region != "":
                loc.append(toRoman(region))
            else:
                loc.append("%")
            if insula != "":
                if insula[0] == "0":
                    insula = insula.replace("0", "")
                loc.append(insula)
            else:
                loc.append("%")
            if prop != "":
                if prop[0] == "0":
                    prop = prop.replace("0", "")
                loc.append(prop)
            else:
                loc.append("%")
            if room != "":
                if room[0] == "0":
                    room = room.replace("0", "")
                loc.append(room)
            else:
                loc.append("%")

            ppmCur.execute(ppmQuery, loc)
            dataTuple = ppmCur.fetchall()
            ppmCur.close()
            newdata = []
            for d in dataTuple:
                d = list(d)
                imgloc = d[2].split("_")
                for i in imgloc:
                    d.append(i)
                newdata.append(d)
        else:
            newdata = "none"

        return render_template('PPM-single-search.html', dbdata=newdata, region=region,
                               insula=insula, prop=prop, room=room)

    else:
        error = "This page is only accessible by logging in."
        return render_template('PPM-single-search.html', error=error)

# Edit one PPM at a time. URL parameter can be PPM ID or UUID
@app.route("/PPM-single")
def showPPMSingle():
    if session.get('PPPlogged_in') and session["PPPlogged_in"]:

        error = ""
        ppmCur = mysql.connection.cursor()
        if (request.args.get('uuid')):
            ppmQuery = "SELECT id, photo_archive_id, description, translated_text, image_path, region, insula, doorway, doorways, room, other_location, volume, page, caption FROM PPM WHERE `id` = '"+request.args['uuid']+"';"
            try:
                ppmCur.execute(ppmQuery)
                data = ppmCur.fetchall()
            except Exception as exception:
                data = 'error'
                error= "You searched for Unique ID "+request.args['uuid']+". That doesn't exist - please add an entry or try again."
            if len(data) < 1:
                data = 'error'
                error= "You searched for Unique ID "+request.args['uuid']+". That doesn't exist - please add an entry or try again."

        elif (request.args.get('location')):
            ppmQuery = "SELECT id, photo_archive_id, description, translated_text, image_path, region, insula, doorway, doorways, room, other_location, volume, page, caption FROM PPM WHERE `location` = '"+str(request.args['location'])+"';"
            try:
                ppmCur.execute(ppmQuery)
                data = ppmCur.fetchall()
            except Exception:
                data = 'error'
                error = "You searched for PPM photo archive id "+request.args['id']+". That doesn't exist - please add an entry or try again."
            if len(data) < 1:
                data = 'error'
                error = "You searched for PPM photo archive id "+request.args['id']+". That doesn't exist - please add an entry or try again."
        else:
            data = 'error'
            error = "Please put a query in the URL using the format <a href='https://workspace.p-lod.umasscreate.net/PPM-single?uuid='>https://workspace.p-lod.umasscreate.net/PPM-single?uuid=</a> or <a href='https://workspace.p-lod.umasscreate.net/PPM-single?location='>https://workspace.p-lod.umasscreate.net/PPM-single?location=</a>."
        ppmCur.close()


        if data != "error":
            newdata = []
            for d in data:
                d = list(d)
                base_url = "http://umassamherst.lunaimaging.com/luna/servlet/as/search?"
                params = ["q=filename="+str(d[0]), "lc=umass~14~14"]
                requesta = requests.get(base_url+"&".join(params))
                result = requesta.json()
                if len(result['results']) > 0:
                    d.append(result['results'][0]['urlSize2'])
                else:
                    d.append("")
                newdata.append(d)
            data = newdata

        return render_template('PPM-single.html', dbdata=data, error=error)

    else:
        error = "This page is only accessible by logging in."
        return render_template('PPM-single.html', dbdata="", error=error)

# Add or edit PPM entry in database
@app.route('/update-ppm-edit', methods=['POST'])
def updatePPMEdit():
    ppmCur = mysql.connection.cursor()
    dictargs = request.form.to_dict()
    date = datetime.now().strftime("%Y-%m-%d")
    sep = dictargs['uuid']
    for k, v in dictargs.items():
        ppmQuery = "INSERT INTO PPM(`id`) SELECT * FROM ( SELECT '" + sep + "' ) AS tmp WHERE NOT EXISTS ( SELECT 1 FROM PPM WHERE `id` = '" + sep + "' ) LIMIT 1;"
        ppmCur.execute(ppmQuery)
        mysql.connection.commit()
        vrep = v.replace('\n', ' ').replace('\r', ' ').replace('\'', "\\'")
        if k == "translated_text":
            ppmQueryA = "UPDATE PPM SET `translated_text` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryA)
        if k == "location":
            ppmQueryB = "UPDATE PPM SET `photo_archive_id` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryB)
        if k == "other":
            ppmQueryC = "UPDATE PPM SET `material` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryC)
        if k == "description":
            ppmQueryD = "UPDATE PPM SET `description` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryD)
        if k == "doorways":
            ppmQueryE = "UPDATE PPM SET `doorways` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryE)
        if k == "image_path":
            ppmQueryF = "UPDATE PPM SET `image_path` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryF)
        if k == "page":
            ppmQueryG = "UPDATE PPM SET `page` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryG)
        if k == "caption":
            ppmQueryH = "UPDATE PPM SET `caption` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryH)
        if k == "region":
            ppmQueryI = "UPDATE PPM SET `region` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryI)
        if k == "insula":
            ppmQueryJ = "UPDATE PPM SET `insula` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryJ)
        if k == "doorway":
            ppmQueryK = "UPDATE PPM SET `doorway` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryK)
        if k == "room":
            ppmQueryL = "UPDATE PPM SET `room` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryL)
        if k == "volume":
            ppmQueryM = "UPDATE PPM SET `volume` = '" + vrep + "' WHERE `id` = '" + sep + "';"
            ppmCur.execute(ppmQueryM)
    mysql.connection.commit()
    ppmCur.close()

    # When new entry added, redirect back to page asking you to add parameters
    if sep.startswith('new'):
        return redirect('/PPM-single')
    # Otherwise, "next" button leads user to next uuid numerically
    else:

        num = sep.split("_")[1]
        nextidint = int(num) + 1
        nextid = sep.split("_")[0] + "_" + str(nextidint)
        return redirect('/PPM-single?uuid='+nextid)

@app.route('/edit_terms', methods=['GET', 'POST'])
def edit_terms():
    return render_template('edit_terms.html', allTerms=getAllTerms(), dbdata="", error="None")

@app.route('/categories', methods=['GET'])
def categories():
    search_data = getSearchTerms()
    cats = getAllCategories()
    categories = []
    for cat in cats:
        for term in search_data:
            if search_data[term]['category'] == cat and search_data[term]['wiki'] != "-":
                try:
                    entity = client.get(search_data[term]['wiki'], load=True)
                    image_prop = client.get('P18')
                    image = entity[image_prop]
                    if image.image_url != "":
                        categories.append({"key": cat, "img_url": image.image_url})
                        break
                except Exception as err:
                    print(err)
    return render_template('categories.html', categories=categories, allTerms=getAllTerms(), dbdata="", error="None")


def getAllCategories():
    search_doc_id = "1kzWEh3v5sEJYARxYVikHdinfKIxbstYhP4HjFTh_dw4"
    res = sheet.values().get(spreadsheetId=search_doc_id, range="Old Vocabulary (v4) with links!A2:H").execute()
    terms = {}
    for row in res['values']:
        terms[row[0]] = {'getty': row[2].replace("\"", "", -1), 'wiki': row[1], 'otherDesc': row[3], 'otherImageUrl': row[4], 'otherUrl': row[5], 'synonyms': row[6], 'category': row[7]}
    search_d = terms
    cats = []
    for term in search_d:
        cat = search_d[term]['category']
        if cat != "" and cat not in cats:
            cats.append(cat)
    return cats

@app.route('/img_search', methods=['GET', 'POST'])
def img_search():
    category = request.args.get("category")
    search_data = getSearchTerms()
    imgUrls = []
    for key in search_data:
        if search_data[key]["category"] == category:
            wikiID = search_data[key]["wiki"]
            if wikiID != "-":
                try:
                    entity = client.get(wikiID, load=True)
                    image_prop = client.get('P18')
                    image = entity[image_prop]
                    imgUrls.append({"key": key, "img_url": image.image_url})
                except Exception as err:
                    imgUrls.append({"key": key, "img_url":  "-" if search_data[key]["otherImageUrl"] == "" else search_data[key]["otherImageUrl"]})
    return render_template('image_search.html', img_urls=imgUrls, allTerms=getAllTerms(), dbdata="", error="None")


# Run Flask app
if __name__ == "__main__":
    app.run()