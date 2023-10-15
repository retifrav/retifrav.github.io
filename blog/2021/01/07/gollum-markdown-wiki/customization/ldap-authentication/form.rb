module OmniAuth
  class Form
    DEFAULT_CSS = File.read(File.expand_path('../form.css', __FILE__))

    attr_accessor :options

    def initialize(options = {})
      options[:title] ||= 'Authentication required'
      options[:header_info] ||= ''
      self.options = options

      @html = ''
      @with_custom_button = false
      @footer = nil
      header(options[:title], options[:header_info])
    end

    def self.build(options = {}, &block)
      form = OmniAuth::Form.new(options)
      if block.arity > 0
        yield form
      else
        form.instance_eval(&block)
      end
      form
    end

    def label_field(text, target)
      # @html << "\n<label for='#{target}'>#{text}:</label>"
      self
    end

    def input_field(type, name)
      # @html << "\n<input type='#{type}' id='#{name}' name='#{name}'/>"
      self
    end

    def text_field(label, name)
      label_field(label, name)
      input_field('text', name)
      self
    end

    def password_field(label, name)
      label_field(label, name)
      input_field('password', name)
      self
    end

    def button(text)
      @with_custom_button = true
      # @html << "\n<tr><th></th><td><button type='submit' class='nice-button small-nice-button'>#{text}</button></td></tr>"
    end

    def html(html)
      @html << html
    end

    # def fieldset(legend, options = {}, &block)
    #   @html << "\n<fieldset#{" style='#{options[:style]}'" if options[:style]}#{" id='#{options[:id]}'" if options[:id]}>\n  <legend>#{legend}</legend>\n"
    #   instance_eval(&block)
    #   @html << "\n</fieldset>"
    #   self
    # end

    def header(title, header_info)
      @html << <<-HTML
      <!DOCTYPE html>
      <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Our Company wiki</title>
        #{css}
        #{header_info}
      </head>
      <body>
      <header>
        <h1><a href="/">Our Company wiki</a></h1>
      </header>
      <main>
        <h2 class="page-header">Login</h2>
        <p>#{title}.</p>
        <form method='post' #{"action='#{options[:url]}' " if options[:url]}noValidate='noValidate' style="margin-top:15px;">
          <table class="listing">
            <tr>
                <th><label for="username">Username:</label></th>
                <td><input id="username" type="text" name="username" class="cool-input" placeholder="alleria"></td>
            </tr>
            <tr>
                <th><label for="password">Password:</label></th>
                <td><input id="password" type="password" name="password" class="cool-input"></td>
            </tr>
            <tr>
                <th></th>
                <td><button type="submit" class="nice-button small-nice-button">Sign-in</button></td>
            </tr>
          </table>
        </form>
      </main>
      HTML
      self
    end

    def footer
      return self if @footer
      @html << <<-HTML
      <footer>
        <span>
          <a target="_blank" href="http://our-company.com/">Our&nbsp;Company</a>
        </span>
        <span>[&nbsp;1999&nbsp;-&nbsp;#{Date.today.year}&nbsp;]</span>
      </footer>

      <script>
        const usernamesForPlaceholder = [
          "alleria",
          "anubarak",
          "anubseran",
          "davion",
          "eredar",
          "jinzakk",
          "kael",
          "kelthuzad",
          "leoric",
          "leshrac",
          "lina",
          "luna",
          "mirana",
          "nessaj",
          "rexxar",
          "rikimaru",
          "rylai",
          "thrall",
          "voljin"
        ]
        const usernameInput = document.getElementById("username");

        window.addEventListener(
          "load",
          (event) =>
          {
            usernameInput.setAttribute(
              "placeholder",
              usernamesForPlaceholder[Math.floor(Math.random() * (usernamesForPlaceholder.length))]
            )
          }
        );
      </script>

      </body>
      </html>
      HTML
      @footer = true
      self
    end

    def to_html
      footer
      @html
    end

    def to_response
      footer
      Rack::Response.new(@html, 200, 'content-type' => 'text/html').finish
    end

  protected

    def css
      "\n<style type='text/css'>#{OmniAuth.config.form_css}</style>"
    end
  end
end
