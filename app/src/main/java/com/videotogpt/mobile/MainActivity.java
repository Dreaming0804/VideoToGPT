package com.videotogpt.mobile;
import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.*;
import android.graphics.Color;
import android.webkit.*;
import android.widget.*;
import android.view.*;
import java.util.*;
import java.text.SimpleDateFormat;

public class MainActivity extends Activity {
  EditText url; WebView web; LinearLayout results; TextView state;
  Set<String> found = new HashSet<>(); volatile String currentPage = "";
  int dp(int i){return (int)(i*getResources().getDisplayMetrics().density+.5f);}
  Button btn(String s){ Button b=new Button(this);b.setText(s);b.setTextSize(13);b.setAllCaps(false);return b;}
  boolean good(String s){return s!=null&&(s.startsWith("https://")||s.startsWith("http://"));}
  boolean media(String s){return s!=null&&s.toLowerCase(Locale.ROOT).matches("(?s).*\\.(mp4|m4v|webm|mov|m3u8|mpd)([?#].*)?$");}
  void msg(String s){runOnUiThread(()->state.setText(s));}
  void showMedia(String raw){
    if(!good(raw)||!media(raw))return;
    runOnUiThread(()->{
      if(found.size()>25||!found.add(raw))return;
      String type=raw.toLowerCase(Locale.ROOT).contains(".m3u8")?"HLS":raw.toLowerCase(Locale.ROOT).contains(".mpd")?"DASH":"视频";
      LinearLayout row=new LinearLayout(this);
      TextView t=new TextView(this);
      String host=Uri.parse(raw).getHost();
      t.setText(type+" · "+host+"\n"+raw.substring(0,Math.min(80,raw.length())));
      t.setTextSize(10);t.setMaxLines(2);
      row.addView(t,new LinearLayout.LayoutParams(0,dp(54),1));
      Button b=btn("下载");
      b.setEnabled(type.equals("视频"));
      if(!type.equals("视频"))b.setText("待支持");
      b.setOnClickListener(v->download(raw));
      row.addView(b,new LinearLayout.LayoutParams(dp(98),dp(50)));
      results.addView(row);msg("已发现 "+found.size()+" 条资源，点击下载可保存 MP4/WebM 直链");
    });
  }
  @android.annotation.SuppressLint("SetJavaScriptEnabled")
  @Override public void onCreate(Bundle saved){
    super.onCreate(saved);
    LinearLayout root=new LinearLayout(this);root.setOrientation(1);
    root.setPadding(dp(10),dp(8),dp(10),dp(4));root.setBackgroundColor(0xfff5f7fb);setContentView(root);
    TextView header=new TextView(this);header.setText("VideoToGPT 安卓测试版 · 0.1");
    header.setTextColor(0xff183f68);header.setTextSize(19);header.setPadding(0,0,0,dp(6));root.addView(header);
    url=new EditText(this);url.setSingleLine(true);url.setTextSize(13);url.setHint("粘贴视频网页地址 https://...");
    root.addView(url,new LinearLayout.LayoutParams(-1,dp(51)));
    LinearLayout buttons=new LinearLayout(this);root.addView(buttons);
    Button open=btn("打开并探测"); Button direct=btn("直链下载");
    buttons.addView(open,new LinearLayout.LayoutParams(0,dp(49),1));
    buttons.addView(direct,new LinearLayout.LayoutParams(0,dp(49),1));
    open.setOnClickListener(v->open());direct.setOnClickListener(v->download(url.getText().toString().trim()));
    state=new TextView(this);state.setTextSize(12);
    state.setText("打开网页后，请点击播放视频。可检测 MP4 / WebM / HLS / DASH；本测试 APK 目前只支持 MP4 / WebM 文件下载。");
    root.addView(state);
    ScrollView sc=new ScrollView(this);results=new LinearLayout(this);results.setOrientation(1);sc.addView(results);
    root.addView(sc,new LinearLayout.LayoutParams(-1,dp(175)));
    web=new WebView(this);
    web.getSettings().setJavaScriptEnabled(true);web.getSettings().setDomStorageEnabled(true);
    web.getSettings().setAllowFileAccess(false);web.getSettings().setAllowContentAccess(false);
    web.getSettings().setMediaPlaybackRequiresUserGesture(true);
    web.addJavascriptInterface(new Object(){
      @JavascriptInterface public void found(String s){showMedia(s);}
    },"VtgObserver");
    web.setWebChromeClient(new WebChromeClient(){
      @Override public void onProgressChanged(WebView w,int p){if(p==100)scan();}
    });
    web.setWebViewClient(new WebViewClient(){
      @Override public void onPageStarted(WebView w,String s,android.graphics.Bitmap b){currentPage=s;}
      @Override public void onPageFinished(WebView w,String s){currentPage=s;scan();w.postDelayed(()->scan(),3000);}
      @Override public WebResourceResponse shouldInterceptRequest(WebView w,WebResourceRequest req){
        String s=req.getUrl().toString();if(media(s))showMedia(s);return null;
      }
    });
    web.setDownloadListener((s,u,d,m,z)->showMedia(s));
    root.addView(web,new LinearLayout.LayoutParams(-1,0,1));
    if(getIntent()!=null)onNewIntent(getIntent());
  }
  void open(){
    String s=url.getText().toString().trim();
    if(!good(s)){msg("请输入完整的 http(s) 网址");return;}
    found.clear();results.removeAllViews();web.loadUrl(s);msg("正在打开网页，请手动点击播放");
  }
  @Override protected void onNewIntent(Intent intent){
    super.onNewIntent(intent);
    String s=intent.getStringExtra(Intent.EXTRA_TEXT);
    if(s==null&&intent.getData()!=null)s=intent.getData().toString();
    if(s!=null){
      java.util.regex.Matcher m=java.util.regex.Pattern.compile("https?://[^\\s<>\"']+").matcher(s);
      if(m.find()){url.setText(m.group());open();}
    }
  }
  void scan(){
    if(web==null)return;
    String js="(function(){try{const f=x=>{if(x&&/^https?:/i.test(x))VtgObserver.found(x)};"+
      "document.querySelectorAll('video,source').forEach(x=>{f(x.src);f(x.currentSrc)});"+
      "document.querySelectorAll('meta[property*=video]').forEach(x=>f(x.content));"+
      "performance.getEntriesByType('resource').forEach(x=>{if(/\\.(mp4|webm|m3u8|mpd)(\\?|$)/i.test(x.name))f(x.name)});"+
      "}catch(e){}return 1})()";
    web.evaluateJavascript(js,null);
  }
  void download(String s){
    if(!good(s)){msg("请输入有效直链");return;}
    if(!media(s)||s.toLowerCase(Locale.ROOT).contains(".m3u8")||s.toLowerCase(Locale.ROOT).contains(".mpd")){
      msg("当前仅支持 MP4 / WebM / MOV 直链；HLS 和 DASH 会在后续版本添加");return;
    }
    try{
      String lower=s.toLowerCase(Locale.ROOT);
      String ext=lower.contains(".webm")?".webm":lower.contains(".mov")?".mov":".mp4";
      String name="VTG_"+new SimpleDateFormat("yyyyMMdd_HHmmss",Locale.US).format(new Date())+ext;
      DownloadManager.Request r=new DownloadManager.Request(Uri.parse(s));
      r.setTitle(name);r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
      r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS,name);
      r.addRequestHeader("User-Agent",web.getSettings().getUserAgentString());
      if(good(currentPage))r.addRequestHeader("Referer",currentPage);
      String cookies=CookieManager.getInstance().getCookie(s);
      if(cookies!=null)r.addRequestHeader("Cookie",cookies);
      DownloadManager dm=(DownloadManager)getSystemService(DOWNLOAD_SERVICE);
      long id=dm.enqueue(r);msg("已添加系统下载任务 "+id+"；请查看通知栏");
    }catch(Exception e){msg("下载失败："+e.getMessage());}
  }
  @Override public void onBackPressed(){if(web.canGoBack())web.goBack();else super.onBackPressed();}
  @Override protected void onDestroy(){if(web!=null)web.destroy();super.onDestroy();}
}