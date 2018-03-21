from xbmcswift2 import Plugin
import re
import requests
import xbmc,xbmcaddon,xbmcvfs,xbmcgui
import xbmcplugin
import base64
import random
import urllib
import sqlite3
import time
import threading
import json
import os,os.path
import stat
import subprocess
from datetime import datetime,timedelta
import uuid

from struct import *
from collections import namedtuple


plugin = Plugin()
big_list_view = False



def addon_id():
    return xbmcaddon.Addon().getAddonInfo('id')

def log(v):
    xbmc.log(repr(v),xbmc.LOGERROR)


def get_icon_path(icon_name):
    if plugin.get_setting('user.icons') == "true":
        user_icon = "special://profile/addon_data/%s/icons/%s.png" % (addon_id(),icon_name)
        if xbmcvfs.exists(user_icon):
            return user_icon
    return "special://home/addons/%s/resources/img/%s.png" % (addon_id(),icon_name)

def remove_formatting(label):
    label = re.sub(r"\[/?[BI]\]",'',label)
    label = re.sub(r"\[/?COLOR.*?\]",'',label)
    return label

def escape( str ):
    str = str.replace("&", "&amp;")
    str = str.replace("<", "&lt;")
    str = str.replace(">", "&gt;")
    str = str.replace("\"", "&quot;")
    return str

def unescape( str ):
    str = str.replace("&lt;","<")
    str = str.replace("&gt;",">")
    str = str.replace("&quot;","\"")
    str = str.replace("&amp;","&")
    return str

def delete(path):
    dirs, files = xbmcvfs.listdir(path)
    for file in files:
        xbmcvfs.delete(path+file)
    for dir in dirs:
        delete(path + dir + '/')
    xbmcvfs.rmdir(path)



@plugin.route('/play/<channelid>')
def play(channelid):
    rpc = '{"jsonrpc":"2.0","method":"Player.Open","id":305,"params":{"item":{"channelid":%s}}}' % channelid
    r = requests.post('http://localhost:8080/jsonrpc',data=rpc)
    #log(r)
    #log(r.content)

@plugin.route('/play_name/<channelname>')
def play_name(channelname):
    channel_urls = plugin.get_storage("channel_urls")
    url = channel_urls.get(channelname)
    if url:
        cmd = ["ffplay",url]
        subprocess.Popen(cmd)


def utc2local (utc):
    epoch = time.mktime(utc.timetuple())
    offset = datetime.fromtimestamp (epoch) - datetime.utcfromtimestamp (epoch)
    return utc + offset

def str2dt(string_date):
    format ='%Y-%m-%d %H:%M:%S'
    try:
        res = datetime.strptime(string_date, format)
    except TypeError:
        res = datetime(*(time.strptime(string_date, format)[0:6]))
    return utc2local(res)

def total_seconds(td):
    return (td.microseconds + (td.seconds + td.days * 24 * 3600) * 10**6) / 10**6

@plugin.route('/jobs')
def jobs():
    jobs = plugin.get_storage("jobs")
    #log(jobs)
    items = []
    for j in sorted(jobs, key= lambda x: x[2]):
        #log((j,jobs[j]))
        channelname,title,starttime,endtime = json.loads(jobs[j])
        items.append({
            'label': "%s - %s[CR][COLOR grey]%s %s - %s[/COLOR]" % (channelname,title,day(str2dt(starttime)),starttime,endtime),
            'path': plugin.url_for(delete_job,job=j)
        })
    return items

@plugin.route('/delete_job/<job>')
def delete_job(job):
    #TODO stop ffmpeg task

    if not (xbmcgui.Dialog().yesno("IPTV Recorder","Cancel Record?")):
        return
    jobs = plugin.get_storage("jobs")

    if windows() and plugin.get_setting('task.scheduler') == 'true':
        cmd = ["schtasks","/delete","/f","/tn",job]
        #log(cmd)
        subprocess.Popen(cmd,shell=True)
    else:
        xbmc.executebuiltin('CancelAlarm(%s,True)' % job)

    directory = "special://profile/addon_data/plugin.video.iptv.recorder/jobs/"
    xbmcvfs.mkdirs(directory)
    pyjob = directory + job + ".py"

    pid = xbmcvfs.File(pyjob+'.uuid').read()
    if pid:
        if windows():
            subprocess.Popen(["taskkill","/im",pid],shell=True)
        else:
            subprocess.Popen(["kill","-9",pid])

    xbmcvfs.delete(pyjob)
    del jobs[job]
    xbmc.executebuiltin('Container.Refresh')

def windows():
    if os.name == 'nt':
        return True
    else:
        return False

def android_get_current_appid():
    with open("/proc/%d/cmdline" % os.getpid()) as fp:
        return fp.read().rstrip("\0")

def ffmpeg_location():
    ffmpeg_src = xbmc.translatePath(plugin.get_setting('ffmpeg'))
    if xbmc.getCondVisibility('system.platform.android'):
        ffmpeg_dst = '/data/data/%s/ffmpeg' % android_get_current_appid()
    else:
        return ffmpeg_src
    if not xbmcvfs.exists(ffmpeg_dst) and ffmpeg_src != ffmpeg_dst:
        xbmcvfs.copy(ffmpeg_src,ffmpeg)

    st = os.stat(ffmpeg)
    if not (st.st_mode | stat.S_IEXEC):
        os.chmod(ffmpeg, st.st_mode | stat.S_IEXEC)


@plugin.route('/record_once/<channelname>/<title>/<starttime>/<endtime>')
def record_once(channelname,title,starttime,endtime):
    #TODO check for ffmpeg process already recording if job is re-added

    channel_urls = plugin.get_storage("channel_urls")
    if not len(channel_urls.keys()):
        m3u()
    if not len(channel_urls.keys()):
        xbmcgui.Dialog().notification("IPTV Recorder","No m3u Channels found!")
    url = channel_urls.get(channelname)

    local_starttime = str2dt(starttime)
    local_endtime = str2dt(endtime)
    label = "%s - %s[CR][COLOR grey]%s - %s[/COLOR]" % (channelname,title,local_starttime,local_endtime)

    before = int(plugin.get_setting('minutes.before') or "0")
    after = int(plugin.get_setting('minutes.after') or "0")
    #log((before,after))
    local_starttime = local_starttime - timedelta(minutes=before)
    local_endtime = local_endtime + timedelta(minutes=after)

    now = datetime.now()
    if (local_starttime < now) and (local_endtime > now):
        local_starttime = now
        immediate = True
    else:
        immediate = False
    #log((immediate,now,local_starttime,local_endtime))

    length = local_endtime - local_starttime
    seconds = total_seconds(length)
    #log((local_starttime,local_endtime,length))

    filename = urllib.quote_plus(label)+'.ts'
    path = os.path.join(xbmc.translatePath(plugin.get_setting('recordings')),filename)
    ffmpeg = ffmpeg_location()
    #ffmpeg = "notepad"
    cmd = [ffmpeg,"-y","-i",url,"-t",str(seconds),"-c","copy",path]
    #log(cmd)

    directory = "special://profile/addon_data/plugin.video.iptv.recorder/jobs/"
    xbmcvfs.mkdirs(directory)
    job = str(uuid.uuid1())
    pyjob = directory + job + ".py"

    f = xbmcvfs.File(pyjob,'wb')
    f.write("import subprocess\n")
    f.write("cmd = %s\n" % repr(cmd))
    f.write("p = subprocess.Popen(cmd,shell=%s)\n" % windows())
    f.write("f = open(r'%s','w+')\n" % xbmc.translatePath(pyjob+'.uuid'))
    f.write("f.write(repr(p.pid))")
    f.close()

    if windows() and plugin.get_setting('task.scheduler') == 'true':

        if immediate:
            cmd = 'RunScript(%s)' % (pyjob)
            xbmc.executebuiltin(cmd)
        else:
            st = "%02d:%02d" % (local_starttime.hour,local_starttime.minute)
            sd = "%02d/%02d/%04d" % (local_starttime.day,local_starttime.month,local_starttime.year)
            cmd = ["schtasks","/create","/f","/tn",job,"/sc","once","/st",st,"/sd",sd,"/tr","%s %s" % (xbmc.translatePath(plugin.get_setting('python')),xbmc.translatePath(pyjob))]
            #log(cmd)
            subprocess.Popen(cmd,shell=True)
    else:
        now = datetime.now()
        diff = local_starttime - now
        minutes = ((diff.days * 86400) + diff.seconds) / 60
        #minutes = 1
        if minutes < 1:
            if local_endtime > now:
                cmd = 'RunScript(%s)' % (pyjob)
                xbmc.executebuiltin(cmd)
            else:
                #pass
                xbmcvfs.delete(pyjob)
                return #TODO
        else:
            cmd = 'AlarmClock(%s,RunScript(%s),%d,True)' % (job,pyjob,minutes)
            #log(cmd)
            xbmc.executebuiltin(cmd)

    jobs = plugin.get_storage("jobs")
    job_description = json.dumps((channelname,title,starttime,endtime))
    jobs[job] = job_description
    xbmc.executebuiltin('Container.Refresh')

@plugin.route('/record_once/<channelname>/<title>/<starttime>/<endtime>')
def record_daily(channelname,title,starttime,endtime):
    pass

@plugin.route('/record_whenever/<channelname>/<title>')
def record_always(channelname,title):
    pass

@plugin.route('/broadcast/<channelname>/<title>/<starttime>/<endtime>')
def broadcast(channelname,title,starttime,endtime):
    #log((channelname,title,starttime,endtime))
    items = []
    #TODO format dates
    items.append({
        'label': "Record Once - %s - %s[CR][COLOR grey]%s - %s[/COLOR]" % (channelname,title,starttime,endtime),
        'path': plugin.url_for(record_once,channelname=channelname,title=title,starttime=starttime,endtime=endtime)
    })

    if False:
        items.append({
            'label': "Record Daily - %s - %s[CR][COLOR grey]%s - %s[/COLOR]" % (channelname,title,starttime,endtime),
            'path': plugin.url_for(record_daily,channelname=channelname,title=title,starttime=starttime,endtime=endtime)
        })
        items.append({
            'label': "Record Always - %s - %s" % (channelname,title),
            'path': plugin.url_for(record_always,channelname=channelname,title=title)
        })
        items.append({
            'label': "Watch Once - %s - %s[CR][COLOR grey]%s - %s[/COLOR]" % (channelname,title,starttime,endtime),
            'path': plugin.url_for(record_once,channelname=channelname,title=title,starttime=starttime,endtime=endtime)
        })
        items.append({
            'label': "Watch Daily - %s - %s[CR][COLOR grey]%s - %s[/COLOR]" % (channelname,title,starttime,endtime),
            'path': plugin.url_for(record_daily,channelname=channelname,title=title,starttime=starttime,endtime=endtime)
        })
        items.append({
            'label': "Watch Always - %s - %s" % (channelname,title),
            'path': plugin.url_for(record_always,channelname=channelname,title=title)
        })
    return items

def day(timestamp):
    if timestamp:
        today = datetime.today()
        tomorrow = today + timedelta(days=1)
        yesterday = today - timedelta(days=1)
        if today.date() == timestamp.date():
            return 'Today'
        elif tomorrow.date() == timestamp.date():
            return 'Tomorrow'
        elif yesterday.date() == timestamp.date():
            return 'Yesterday'
        else:
            return timestamp.strftime("%A")


@plugin.route('/channel/<channelname>/<channelid>')
def channel(channelname,channelid):
    channel_thumbnails = plugin.get_storage("channel_thumbnails")
    thumbnail = channel_thumbnails.get(channelname)
    jobs = plugin.get_storage("jobs")
    job_descriptions = {jobs[x]:x for x in jobs}
    rpc = '{"jsonrpc":"2.0","method":"PVR.GetBroadcasts","id":1,"params":{"channelid":%s,"properties":["title","plot","plotoutline","starttime","endtime","runtime","progress","progresspercentage","genre","episodename","episodenum","episodepart","firstaired","hastimer","isactive","parentalrating","wasactive","thumbnail","rating"]}}' % channelid
    r = requests.get('http://localhost:8080/jsonrpc?request='+urllib.quote_plus(rpc))
    content = r.content
    j = json.loads(content)
    #log(j)
    broadcasts = j.get('result').get('broadcasts')
    if not broadcasts:
        return
    items = []
    for b in broadcasts:
        #log(b)
        starttime = b.get('starttime')
        endtime = b.get('endtime')
        title = b.get('title')
        plot = b.get('plot')
        genre = ','.join(b.get('genre'))
        job_description = json.dumps((channelname,title,starttime,endtime))
        broadcastid=str(b.get('broadcastid'))
        #log(starttime)
        start = str2dt(starttime)
        #log(start)
        recording = ""
        if job_description in job_descriptions:
            recording = "[COLOR red]RECORD[/COLOR]"
        label = "[COLOR grey]%s %02d:%02d[/COLOR] %s %s" % (day(start),start.hour,start.minute,b.get('label'),recording)
        context_items = []
        if recording:
            context_items.append(("Cancel Record" , 'XBMC.RunPlugin(%s)' % (plugin.url_for(delete_job,job=job_descriptions[job_description]))))
        else:
            context_items.append(("Record Once" , 'XBMC.RunPlugin(%s)' % (plugin.url_for(record_once,channelname=channelname.encode("utf8"),title=title.encode("utf8"),starttime=starttime,endtime=endtime))))
        context_items.append(("Play PVR" , 'XBMC.RunPlugin(%s)' % (plugin.url_for(play,channelid=channelid))))
        context_items.append(("Play ffplay" , 'XBMC.RunPlugin(%s)' % (plugin.url_for(play_name,channelname=channelname.encode("utf8")))))
        items.append({
            'label': label,
            'path': plugin.url_for(broadcast,channelname=channelname.encode("utf8"),title=title.encode("utf8"),starttime=starttime,endtime=endtime),
            'thumbnail': thumbnail,
            'context_menu': context_items,
            'info_type': 'Video',
            'info':{"title": channelname, "plot":plot,"genre":genre}
        })
    return items


@plugin.route('/group/<channelgroupid>')
def group(channelgroupid):
    channel_urls = plugin.get_storage("channel_urls")
    channel_thumbnails = plugin.get_storage("channel_thumbnails")
    rpc = '{"jsonrpc":"2.0","method":"PVR.GetChannels","id":1,"params":{"channelgroupid":%s,"properties":["thumbnail","channeltype","hidden","locked","channel","lastplayed","broadcastnow","broadcastnext"]}}' % channelgroupid
    r = requests.get('http://localhost:8080/jsonrpc?request='+urllib.quote_plus(rpc))
    content = r.content
    j = json.loads(content)
    #log(j)
    channels = j.get('result').get('channels')
    items = []
    for c in channels:
        #log(c)
        label = c.get('label')
        channelname = c.get('channel')
        channelid = str(c.get('channelid'))
        thumbnail = c.get('thumbnail')
        channel_thumbnails[channelname] = thumbnail
        #log((name,cid))
        context_items = []
        context_items.append(("Play RPC" , 'XBMC.RunPlugin(%s)' % (plugin.url_for(play,channelid=channelid))))
        context_items.append(("Play ffplay" , 'XBMC.RunPlugin(%s)' % (plugin.url_for(play_name,channelname=channelname))))
        items.append({
            'label': channelname,
            'path': plugin.url_for(channel,channelname=channelname,channelid=channelid),
            'context_menu': context_items,
            'thumbnail': thumbnail,
        })
    return items

@plugin.route('/groups')
def groups():
    rpc = '{"jsonrpc":"2.0","method":"PVR.GetChannelGroups","id":1,"params":{"channeltype":"tv"}}'
    r = requests.get('http://localhost:8080/jsonrpc?request='+urllib.quote_plus(rpc))
    content = r.content
    j = json.loads(content)
    #log(j)
    channelgroups = j.get('result').get('channelgroups')
    items = []
    for channelgroup in channelgroups:
        #log(channelgroup)
        items.append({
            'label': channelgroup.get('label'),
            'path': plugin.url_for(group,channelgroupid=str(channelgroup.get('channelgroupid')))

        })
    return items

@plugin.route('/m3u')
def m3u():
    m3uUrl = xbmcaddon.Addon('pvr.iptvsimple').getSetting('m3uUrl')
    #log(m3uUrl)
    m3uFile = 'special://profile/addon_data/plugin.video.iptv.recorder/channels.m3u'
    xbmcvfs.copy(m3uUrl,m3uFile)
    f = xbmcvfs.File(m3uFile)
    data = f.read()
    #log(data)
    channel_urls = plugin.get_storage("channel_urls")
    channel_urls.clear()
    channels = re.findall('#EXTINF:(.*?)\n(.*?)\n',data,flags=(re.I|re.DOTALL))
    for channel in channels:
        #log(channel)
        match = re.search('tvg-name="(.*?)"',channel[0])
        if match:
            name = match.group(1)
        else:
            name = channel[0].rsplit(',',1)[-1]
        url = channel[1]
        #log((name,url))
        channel_urls[name] = url
    #channel_urls.sync()

@plugin.route('/service')
def service():
    #log("SERVICE")
    pass

@plugin.route('/')
def index():
    items = []
    context_items = []

    items.append(
    {
        'label': "Channel Groups",
        'path': plugin.url_for('groups'),
        'thumbnail':get_icon_path('settings'),
        'context_menu': context_items,
    })

    items.append(
    {
        'label': "Recording Jobs",
        'path': plugin.url_for('jobs'),
        'thumbnail':get_icon_path('settings'),
        'context_menu': context_items,
    })

    items.append(
    {
        'label': "(Re)Load Channel m3u",
        'path': plugin.url_for('m3u'),
        'thumbnail':get_icon_path('settings'),
        'context_menu': context_items,
    })

    items.append(
    {
        'label': "Service",
        'path': plugin.url_for('service'),
        'thumbnail':get_icon_path('settings'),
        'context_menu': context_items,
    })

    return items

if __name__ == '__main__':
    plugin.run()