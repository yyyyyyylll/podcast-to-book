from multi.parsers.wechat_public_article import parse_public_wechat_article


def test_parse_public_wechat_article_preserves_structural_paragraphs():
    html = """
    <html>
      <body>
        <h1 class="rich_media_title">测试文章</h1>
        <div id="js_name">燃点艺术 DART</div>
        <div id="js_content">
          <section>
            <span>第一段</span><span>继续。</span>
          </section>
          <section>
            <div><span>第二段</span><br><span>换行后。</span></div>
          </section>
          <p>第三段</p>
        </div>
      </body>
    </html>
    """

    article = parse_public_wechat_article(html)

    assert article.text == "第一段继续。\n\n第二段\n\n换行后。\n\n第三段"
